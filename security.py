from datetime import datetime, timedelta, timezone

from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def _get_settings():
    from settings import settings

    return settings


def _get_jose():
    try:
        from jose import JWTError, jwt
    except ModuleNotFoundError as exc:
        raise RuntimeError("Le package 'python-jose' est requis pour les fonctions JWT.") from exc

    return JWTError, jwt


def create_access_token(payload: dict) -> str:
    settings = _get_settings()
    _, jwt = _get_jose()
    to_encode = payload.copy()
    to_encode["exp"] = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRES_MIN)
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALG)


def decode_token(token: str) -> dict:
    settings = _get_settings()
    JWTError, jwt = _get_jose()
    try:
        data = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALG])
        if not isinstance(data, dict):
            raise ValueError("Invalid token")
        return data
    except JWTError as exc:
        raise ValueError("Invalid token") from exc
