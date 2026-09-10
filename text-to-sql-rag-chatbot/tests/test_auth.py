import os
import pytest
from unittest.mock import MagicMock
from fastapi import HTTPException
from src.config import settings

# Ensure a test JWT secret is set for testing
settings.JWT_SECRET = "test-jwt-secret-key-that-is-over-32-chars-long"

from src.auth import (
    hash_password,
    verify_password,
    create_access_token,
    decode_token,
    get_current_user
)

def test_password_hashing_and_verification():
    raw = "MySecurePassword123!"
    hashed = hash_password(raw)
    assert hashed != raw
    assert verify_password(raw, hashed) is True
    assert verify_password("WrongPassword", hashed) is False

def test_token_creation_and_decoding():
    token = create_access_token(user_id=42, username="alice")
    payload = decode_token(token)
    assert payload["sub"] == "42"
    assert payload["username"] == "alice"

def test_get_current_user_from_cookie():
    token = create_access_token(user_id=10, username="bob")
    request = MagicMock()
    request.cookies = {"access_token": token}
    request.headers = {}

    user = get_current_user(request=request, credentials=None)
    assert user["id"] == 10
    assert user["username"] == "bob"

def test_get_current_user_from_bearer_header():
    token = create_access_token(user_id=20, username="carol")
    request = MagicMock()
    request.cookies = {}
    
    credentials = MagicMock()
    credentials.credentials = token

    user = get_current_user(request=request, credentials=credentials)
    assert user["id"] == 20
    assert user["username"] == "carol"

def test_get_current_user_unauthenticated():
    request = MagicMock()
    request.cookies = {}

    with pytest.raises(HTTPException) as exc:
        get_current_user(request=request, credentials=None)
    assert exc.value.status_code == 401

def test_get_current_user_invalid_token():
    request = MagicMock()
    request.cookies = {"access_token": "invalid.jwt.token"}

    with pytest.raises(HTTPException) as exc:
        get_current_user(request=request, credentials=None)
    assert exc.value.status_code == 401
