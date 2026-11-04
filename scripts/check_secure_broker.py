"""Read-only live checks for the original matching broker behind its TLS proxy."""
import json
import ssl
from pathlib import Path
import httpx
from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
bundles = root / "certs/unified"
admin = dotenv_values(root / ".env.admin")["BROKER_ADMIN_TOKEN"]
services = json.loads(dotenv_values(root / ".env")["PPRL_BROKER_CREDENTIALS"])

def context(site=None, trust=True):
    ctx = ssl.create_default_context(cafile=str(bundles / "broker/ca.crt")) if trust else ssl.create_default_context()
    ctx.minimum_version = ctx.maximum_version = ssl.TLSVersion.TLSv1_3
    if site:
        bundle = bundles / f"hospital{site}"
        ctx.load_cert_chain(bundle / "client.crt", bundle / "client.key")
    return ctx

with httpx.Client(verify=context(), trust_env=False) as client:
    page = client.get("https://localhost:9443/")
    assert page.status_code == 200
    assert 'id="root"' in page.text and '/assets/' in page.text
    assert client.get("https://localhost:9443/api/v1/admin/overview").status_code == 401
    assert client.get("https://localhost:9443/api/v1/admin/overview", headers={"Authorization": f"Bearer {admin}"}).status_code == 200
    assert client.get("https://localhost:9443/api/v1/submissions/test").status_code == 404
    response = client.get("https://localhost:8443/api/v1/submissions/test", headers={"X-Client-Verify": "SUCCESS", "X-Proxy-Role": "hospital", "Authorization": f"Bearer {services['1']}"})
    assert response.status_code == 400
print("PASS: original React dashboard, administrator login, endpoint isolation and missing-certificate rejection")
for site in ("1", "2", "3"):
    with httpx.Client(verify=context(site), trust_env=False) as client:
        url = "https://localhost:8443/api/v1/submissions/00000000-0000-0000-0000-000000000000"
        assert client.get(url, headers={"Authorization": f"Bearer {services[site]}"}).status_code == 404
        other = "2" if site == "1" else "1"
        assert client.get(url, headers={"Authorization": f"Bearer {services[other]}"}).status_code == 403
        assert client.get("https://localhost:8443/api/v1/admin/overview", headers={"Authorization": f"Bearer {admin}"}).status_code == 404
print("PASS: all hospital certificates accepted; certificate/credential mismatches rejected")
try:
    with httpx.Client(verify=context(trust=False), trust_env=False) as client:
        client.get("https://localhost:9443/")
except httpx.ConnectError:
    print("PASS: untrusted broker CA rejected")
else:
    raise AssertionError("Unexpected trust of the private CA")
