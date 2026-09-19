"""
Unit and Integration Tests for Authentication and JWT Module.
"""

from datetime import datetime, timedelta, timezone
import pytest
import jwt
from fastapi import HTTPException
from pydantic import ValidationError

from src.config import Settings
from src.auth import (
    hash_password,
    verify_password,
    create_access_token,
    decode_access_token,
    AuthRequest
)


def test_password_hashing_and_verification():
    plain = "SuperSecurePassword123!"
    hashed = hash_password(plain)

    assert hashed != plain
    assert verify_password(plain, hashed) is True
    assert verify_password("WrongPassword123!", hashed) is False


def test_jwt_creation_and_decoding():
    token = create_access_token(user_id=42, username="alice")
    payload = decode_access_token(token)

    assert payload["sub"] == "42"
    assert payload["username"] == "alice"
    assert "exp" in payload


def test_tampered_jwt_token_raises_401():
    token = create_access_token(user_id=1, username="bob")
    tampered = token[:-4] + "abcd"

    with pytest.raises(HTTPException) as exc:
        decode_access_token(tampered)
    assert exc.value.status_code == 401
    assert "Invalid or tampered" in exc.value.detail


def test_expired_jwt_token_raises_401():
    # Construct an explicitly expired token
    expired_time = datetime.now(timezone.utc) - timedelta(hours=1)
    payload = {
        "sub": "10",
        "username": "charlie",
        "exp": expired_time,
        "iat": expired_time - timedelta(minutes=5)
    }
    secret = "a" * 32
    token = jwt.encode(payload, secret, algorithm="HS256")

    with pytest.MonkeyPatch.context() as m:
        from src.config import settings
        m.setattr(settings, "JWT_SECRET", secret)
        with pytest.raises(HTTPException) as exc:
            decode_access_token(token)
        assert exc.value.status_code == 401
        assert "expired" in exc.value.detail.lower()


def test_weak_jwt_secret_refuses_startup():
    # Secret less than 32 characters should fail validation
    with pytest.raises(ValidationError) as exc:
        Settings(JWT_SECRET="short_secret", QUERY_DB_PASSWORD="pass", _env_file=None)
    assert "at least 32 characters" in str(exc.value)


def test_empty_jwt_secret_refuses_startup():
    with pytest.raises(ValidationError) as exc:
        Settings(JWT_SECRET="", QUERY_DB_PASSWORD="pass", _env_file=None)
    assert "at least 32 characters" in str(exc.value)


def test_missing_jwt_secret_refuses_startup(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(ValidationError):
        Settings(QUERY_DB_PASSWORD="pass", _env_file=None)


def test_missing_query_db_password_refuses_startup(monkeypatch):
    monkeypatch.delenv("QUERY_DB_PASSWORD", raising=False)
    with pytest.raises(ValidationError):
        Settings(JWT_SECRET="a" * 32, _env_file=None)


# --- Input Validation Tests (Pydantic Models) ---

def test_auth_request_valid():
    req = AuthRequest(username="valid_user-123", password="Password1234!")
    assert req.username == "valid_user-123"


@pytest.mark.parametrize("invalid_username", [
    "ab",                       # too short (<3)
    "a" * 51,                   # too long (>50)
    "user name",                # contains space
    "user@domain.com",          # contains @
    "user$name",                # invalid symbol
])
def test_auth_request_invalid_username(invalid_username):
    with pytest.raises(ValidationError):
        AuthRequest(username=invalid_username, password="ValidPassword123")


@pytest.mark.parametrize("invalid_password", [
    "short",                    # too short (<8)
    "1234567",                  # 7 characters
    "a" * 129,                  # too long (>128)
])
def test_auth_request_invalid_password(invalid_password):
    with pytest.raises(ValidationError):
        AuthRequest(username="valid_user", password=invalid_password)
