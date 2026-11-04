import hmac
import json
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from pathlib import Path
from urllib.parse import unquote
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from .config import credential_hash
from .models import Hospital

bearer = HTTPBearer(auto_error=False)


def digest(auth):
    if auth is None or auth.scheme.lower() != "bearer":
        raise HTTPException(401, "Service credential required", headers={"WWW-Authenticate": "Bearer"})
    return credential_hash(auth.credentials)


def hospital_identity(request: Request, auth: HTTPAuthorizationCredentials = Depends(bearer)):
    certificate_site = None
    allowlist_path = request.app.state.settings.certificate_allowlist
    if allowlist_path:
        if request.headers.get("x-proxy-role") != "hospital" or request.headers.get("x-client-verify") != "SUCCESS":
            raise HTTPException(403, "Verified hospital certificate required")
        try:
            certificate = x509.load_pem_x509_certificate(unquote(request.headers.get("x-client-cert", "")).encode())
            identities = json.loads(Path(allowlist_path).read_text(encoding="utf-8"))
            certificate_site = identities.get(certificate.fingerprint(hashes.SHA256()).hex())
        except (ValueError, OSError):
            raise HTTPException(403, "Invalid client certificate") from None
        if not certificate_site:
            raise HTTPException(403, "Hospital certificate is not allowlisted")
        certificate_site = certificate_site.removeprefix("hospital")
    value = digest(auth)
    with request.app.state.sessions() as db:
        for hospital in db.scalars(select(Hospital).where(Hospital.enabled.is_(True))):
            if hmac.compare_digest(value, hospital.credential_hash):
                if certificate_site is not None and certificate_site != hospital.id:
                    raise HTTPException(403, "Certificate and service credential identify different hospitals")
                return hospital.id
    raise HTTPException(401, "Invalid service credential")


def administrator(request: Request, auth: HTTPAuthorizationCredentials = Depends(bearer)):
    if request.app.state.settings.certificate_allowlist and request.headers.get("x-proxy-role") != "operator":
        raise HTTPException(403, "Use the operator HTTPS endpoint")
    if not hmac.compare_digest(digest(auth), request.app.state.settings.admin_hash):
        raise HTTPException(401, "Invalid administrator credential")
