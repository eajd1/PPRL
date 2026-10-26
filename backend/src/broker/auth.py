import hmac
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
    value = digest(auth)
    with request.app.state.sessions() as db:
        for hospital in db.scalars(select(Hospital).where(Hospital.enabled.is_(True))):
            if hmac.compare_digest(value, hospital.credential_hash):
                return hospital.id
    raise HTTPException(401, "Invalid service credential")


def administrator(request: Request, auth: HTTPAuthorizationCredentials = Depends(bearer)):
    if not hmac.compare_digest(digest(auth), request.app.state.settings.admin_hash):
        raise HTTPException(401, "Invalid administrator credential")
