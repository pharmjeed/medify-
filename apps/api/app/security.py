import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from sqlalchemy import text
from sqlalchemy.orm import Session
from .config import settings
from .database import get_db
from .models import Role, User


pwd_context = PasswordHasher()
bearer = HTTPBearer(auto_error=False)


def set_tenant_context(db: Session, facility_id: str) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT set_config('app.current_facility', :facility_id, true)"), {"facility_id": facility_id})


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return pwd_context.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False


def create_token(user: User, minutes: int | None = None) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=minutes or settings.access_token_minutes)
    return jwt.encode({"sub": user.id, "facility_id": user.facility_id, "role": user.role.value, "exp": exp}, settings.secret_key, algorithm="HS256")


def create_refresh_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(48)
    return token, hashlib.sha256(token.encode()).hexdigest()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except JWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="MDF-4012") from exc


def current_user(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)) -> User:
    raw_token = credentials.credentials if credentials else request.cookies.get("medify_access")
    if not raw_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="MDF-4012")
    payload = decode_token(raw_token)
    set_tenant_context(db, payload.get("facility_id", ""))
    user = db.get(User, payload.get("sub"))
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="MDF-4013")
    if user.facility_id != payload.get("facility_id"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="MDF-4031")
    return user


def require(role: Role):
    def dependency(user: User = Depends(current_user)) -> User:
        if user.role != role:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="MDF-4031")
        return user
    return dependency
