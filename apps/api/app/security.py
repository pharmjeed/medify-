from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from .config import settings
from .database import get_db
from .models import Role, User


pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def create_token(user: User, minutes: int | None = None) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=minutes or settings.access_token_minutes)
    return jwt.encode({"sub": user.id, "facility_id": user.facility_id, "role": user.role.value, "exp": exp}, settings.secret_key, algorithm="HS256")


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)) -> User:
    if not credentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="MDF-4012")
    try:
        payload = jwt.decode(credentials.credentials, settings.secret_key, algorithms=["HS256"])
        user_id = payload.get("sub")
    except JWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="MDF-4012") from exc
    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="MDF-4013")
    return user


def require(role: Role):
    def dependency(user: User = Depends(current_user)) -> User:
        if user.role != role:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="MDF-4031")
        return user
    return dependency

