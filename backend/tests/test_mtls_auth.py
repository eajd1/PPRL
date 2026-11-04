"""Certificate identity is checked in addition to the existing service credential."""
from datetime import datetime, timedelta, timezone
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import hashlib
import json
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from src.broker.auth import administrator, hospital_identity
from src.broker.config import credential_hash


class Session:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def scalars(self, query):
        return [SimpleNamespace(id="1", credential_hash=credential_hash("hospital-secret"))]


def request(tmp_path, headers, site="hospital1"):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "not-an-identity")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
            .sign(key, hashes.SHA256()))
    der = cert.public_bytes(serialization.Encoding.DER)
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    path = tmp_path / "allowlist.json"
    path.write_text(json.dumps({hashlib.sha256(der).hexdigest(): site}))
    settings = SimpleNamespace(certificate_allowlist=str(path), admin_hash=credential_hash("admin-secret"))
    state = SimpleNamespace(settings=settings, sessions=Session)
    return SimpleNamespace(headers={**headers, "x-client-cert": quote(pem)}, app=SimpleNamespace(state=state))


def test_service_credential_alone_cannot_bypass_mtls(tmp_path):
    req = request(tmp_path, {})
    with pytest.raises(HTTPException) as error:
        hospital_identity(req, HTTPAuthorizationCredentials(scheme="Bearer", credentials="hospital-secret"))
    assert error.value.status_code == 403


def test_certificate_identity_must_match_credential(tmp_path):
    headers = {"x-proxy-role": "hospital", "x-client-verify": "SUCCESS"}
    auth = HTTPAuthorizationCredentials(scheme="Bearer", credentials="hospital-secret")
    assert hospital_identity(request(tmp_path, headers), auth) == "1"
    with pytest.raises(HTTPException) as error:
        hospital_identity(request(tmp_path, headers, site="hospital2"), auth)
    assert error.value.status_code == 403


def test_hospital_proxy_cannot_access_administrator_routes(tmp_path):
    with pytest.raises(HTTPException) as error:
        administrator(request(tmp_path, {"x-proxy-role": "hospital"}),
                      HTTPAuthorizationCredentials(scheme="Bearer", credentials="admin-secret"))
    assert error.value.status_code == 403
