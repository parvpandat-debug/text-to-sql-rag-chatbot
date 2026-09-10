from unittest.mock import patch, MagicMock
import pytest
from fastapi.testclient import TestClient

from src.config import settings

# Configure test environment secrets
settings.JWT_SECRET = "test-jwt-secret-key-that-is-over-32-chars-long"
settings.GOOGLE_API_KEY = "test-google-api-key"
settings.DB_USER = "test_user"
settings.DB_PASSWORD = "test_password"
settings.DB_NAME = "test_db"
settings.RATE_LIMIT_CHAT = "3/minute"

import server
from server import app

@pytest.fixture
def client():
    # Mock database and LLM connections during API tests
    with patch("server.get_db") as mock_get_db, \
         patch("server.get_llm") as mock_get_llm, \
         patch("server.build_sql_chain") as mock_sql_chain, \
         patch("server.build_full_chain") as mock_full_chain:
        
        mock_db_instance = MagicMock()
        mock_db_instance.get_usable_table_names.return_value = ["orders", "products"]
        mock_get_db.return_value = mock_db_instance
        
        with TestClient(app) as test_client:
            yield test_client

def test_health_endpoint(client):
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"

def test_input_validation_auth_failure(client):
    # Username too short (< 3 chars)
    res = client.post("/api/auth/register", json={"username": "ab", "password": "valid_password_123"})
    assert res.status_code == 422

    # Password too short (< 8 chars)
    res = client.post("/api/auth/register", json={"username": "validuser", "password": "123"})
    assert res.status_code == 422

def test_input_validation_chat_question_limit(client):
    # Create valid auth token
    from src.auth import create_access_token
    token = create_access_token(user_id=1, username="testuser")
    client.cookies.set("access_token", token)

    # Question exceeds 1000 characters
    long_question = "a" * 1001
    res = client.post("/api/chat", json={"question": long_question})
    assert res.status_code == 422

def test_auth_me_with_cookie(client):
    from src.auth import create_access_token
    token = create_access_token(user_id=99, username="john_doe")
    client.cookies.set("access_token", token)

    res = client.get("/api/auth/me")
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == 99
    assert data["username"] == "john_doe"

def test_auth_logout(client):
    from src.auth import create_access_token
    token = create_access_token(user_id=99, username="john_doe")
    client.cookies.set("access_token", token)

    res = client.post("/api/auth/logout")
    assert res.status_code == 200
    # Cookie should be deleted/cleared in response
    set_cookie_header = res.headers.get("set-cookie", "")
    assert "access_token=" in set_cookie_header

def test_rate_limiting_chat(client):
    from src.auth import create_access_token
    token = create_access_token(user_id=55, username="rate_limited_user")
    client.cookies.set("access_token", token)

    # Mock chat execution dependencies
    mock_sql_chain = MagicMock()
    mock_sql_chain.invoke.return_value = "SELECT * FROM orders LIMIT 50"
    mock_full_chain = MagicMock()
    mock_full_chain.invoke.return_value = "Here are your orders."

    server.sql_chain = mock_sql_chain
    server.full_chain = mock_full_chain

    with patch("server.engine") as mock_engine:
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        mock_engine.begin.return_value.__enter__.return_value = mock_conn

        # Execute 3 calls (allowed under 3/minute test limit)
        for i in range(3):
            res = client.post("/api/chat", json={"question": f"Question {i}"})
            assert res.status_code == 200

        # 4th call must be throttled with 429 Too Many Requests
        res = client.post("/api/chat", json={"question": "Question 4"})
        assert res.status_code == 429
