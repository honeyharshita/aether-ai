import datetime as dt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

ROLES = [
    "ADMIN", "PROJECT_MANAGER", "DEVELOPER", "QA_ENGINEER",
    "SECURITY_ENGINEER", "DATA_SCIENTIST", "VIEWER",
]


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_token(subject: str, role: str, expires_delta: dt.timedelta, token_type: str = "access") -> str:
    expire = dt.datetime.utcnow() + expires_delta
    payload = {"sub": subject, "role": role, "type": token_type, "exp": expire}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_access_token(user: User) -> str:
    return create_token(user.id, user.role, dt.timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES), "access")


def create_refresh_token(user: User) -> str:
    return create_token(user.id, user.role, dt.timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS), "refresh")


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    payload = decode_token(token)
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Wrong token type")
    user = db.query(User).filter(User.id == payload["sub"]).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user


def require_roles(*allowed_roles: str):
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed_roles and user.role != "ADMIN":
            raise HTTPException(status_code=403, detail="Insufficient role permissions")
        return user
    return dependency
