from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from security import decode_token

bearer = HTTPBearer(auto_error=False)

def require_user(creds: HTTPAuthorizationCredentials = Depends(bearer)) -> dict:
    # 401 neutre (ne révèle rien)
    if not creds or creds.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Non autorisé")

    try:
        return decode_token(creds.credentials)
    except ValueError:
        raise HTTPException(status_code=401, detail="Non autorisé")


def require_role(*roles: str):
    def _inner(user: dict = Depends(require_user)) -> dict:
        if user.get("role") not in roles:
            raise HTTPException(status_code=403, detail="Accès refusé")
        return user

    return _inner
