from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
import bcrypt
import jwt
from fastapi import HTTPException, Security, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from src.config import settings

ALGORITHM = "HS256"
# Set auto_error=False so we can fall back to cookie authentication
bearer_scheme = HTTPBearer(auto_error=False)

def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))

def create_access_token(user_id: int, username: str) -> str:
    if not settings.JWT_SECRET:
        raise RuntimeError("JWT_SECRET is not configured.")
    expires = datetime.now(timezone.utc) + timedelta(days=7)
    payload = {"sub": str(user_id), "username": username, "exp": expires}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)

def decode_token(token: str) -> Dict[str, Any]:
    if not settings.JWT_SECRET:
        raise RuntimeError("JWT_SECRET is not configured.")
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM])

def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme)
) -> Dict[str, Any]:
    """Resolves authenticated user from httpOnly cookie or Authorization Bearer header."""
    token: Optional[str] = None

    # 1. Primary: Read from secure httpOnly cookie
    if "access_token" in request.cookies:
        token = request.cookies.get("access_token")
    # 2. Fallback: Read from Authorization: Bearer header (for API/CLI/testing)
    elif credentials and credentials.credentials:
        token = credentials.credentials

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please log in."
        )

    try:
        payload = decode_token(token)
        user_id_str = payload.get("sub")
        username = payload.get("username")
        if user_id_str is None or username is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token claims."
            )
        return {"id": int(user_id_str), "username": username}
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has expired. Please log in again."
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token."
        )