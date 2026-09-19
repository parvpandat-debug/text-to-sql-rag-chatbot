"""
Authentication and JWT Security Module.

Implements bcrypt password hashing, secure JWT access token generation/validation,
and strict Pydantic models for incoming credentials.
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, Any
import bcrypt
import jwt
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from src.config import settings

security = HTTPBearer()


# --- Pydantic Validation Models ---

class AuthRequest(BaseModel):
    """Strict input validation for user registration and authentication."""
    username: str = Field(
        min_length=3,
        max_length=50,
        pattern=r"^[a-zA-Z0-9_\-\.]+$",
        description="Username must be 3-50 alphanumeric characters, hyphens, underscores, or dots."
    )
    password: str = Field(
        min_length=8,
        max_length=128,
        description="Password must be between 8 and 128 characters."
    )


class QueryRequest(BaseModel):
    """Strict input validation for user chat queries."""
    question: str = Field(
        min_length=1,
        max_length=1000,
        description="User natural language question bounded between 1 and 1000 characters."
    )


# --- Cryptographic Utilities ---

def hash_password(password: str) -> str:
    """Hashes a password with bcrypt and automated salt generation."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plain password against stored bcrypt hash."""
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8")
    )


def create_access_token(user_id: int, username: str) -> str:
    """Generates a signed JWT with expiration timestamp."""
    expires = datetime.now(timezone.utc) + timedelta(days=settings.JWT_EXPIRATION_DAYS)
    payload = {
        "sub": str(user_id),
        "username": username,
        "exp": expires,
        "iat": datetime.now(timezone.utc)
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> Dict[str, Any]:
    """Decodes and validates JWT token signature and expiration."""
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM]
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session token has expired. Please sign in again."
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or tampered authentication token."
        )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(security)
) -> Dict[str, Any]:
    """FastAPI dependency for authenticating Bearer token requests."""
    token = credentials.credentials
    payload = decode_access_token(token)
    user_id = payload.get("sub")
    username = payload.get("username")
    if user_id is None or username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed token payload."
        )
    return {"id": int(user_id), "username": username}