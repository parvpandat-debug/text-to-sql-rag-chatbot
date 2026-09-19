"""
Real MySQL Database Integration Tests for Read-Only Isolation.

Proves with real MySQL database connections that:
1. chatbot_readonly CANNOT execute INSERT or DROP statements.
2. chatbot_readonly CANNOT execute SELECT on users (denied with Error 1142).
3. Long queries (SLEEP beyond timeout) are aborted by max_execution_time.
4. chatbot_readonly CAN execute valid SELECT queries on business tables.

Marked as 'integration'. Runs automatically in CI with Docker MySQL 8.0.
"""

import os
import pytest
import pymysql
from src.config import settings


def _connect_readonly():
    """Attempts real MySQL connection as chatbot_readonly user; skips if DB unavailable locally, fails if CI."""
    try:
        conn = pymysql.connect(
            host=settings.DB_HOST,
            port=settings.DB_PORT,
            user=settings.QUERY_DB_USER,
            password=settings.QUERY_DB_PASSWORD,
            database=settings.DB_NAME,
            connect_timeout=3
        )
        # Apply session hardening as configured in production
        with conn.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY;")
            cursor.execute(f"SET SESSION max_execution_time={settings.QUERY_MAX_EXECUTION_TIME_MS};")
        return conn
    except Exception as e:
        if os.environ.get("CI") or settings.ENVIRONMENT == "ci" or os.environ.get("FAIL_ON_INTEGRATION_SKIP") == "1":
            pytest.fail(f"Integration tests cannot be skipped in CI! MySQL chatbot_readonly connection failed: {e}")
        pytest.skip(f"Live MySQL chatbot_readonly connection not available ({e}). Skipping integration test.")


@pytest.fixture
def readonly_connection():
    conn = _connect_readonly()
    yield conn
    conn.close()



@pytest.mark.integration
def test_readonly_user_select_business_tables_succeeds(readonly_connection):
    """Proves chatbot_readonly can successfully query business tables."""
    with readonly_connection.cursor() as cursor:
        cursor.execute("SELECT id, name, price FROM products LIMIT 5;")
        rows = cursor.fetchall()
        assert isinstance(rows, (tuple, list))


@pytest.mark.integration
def test_readonly_user_insert_fails(readonly_connection):
    """Proves chatbot_readonly is blocked from inserting rows."""
    with readonly_connection.cursor() as cursor:
        with pytest.raises(Exception) as exc:
            cursor.execute("INSERT INTO products (name, category, price, stock) VALUES ('Hacked', 'None', 0.0, 0);")
        # Blocked by Error 1142 (command denied) or Error 1792 (READ ONLY transaction)
        err_msg = str(exc.value).lower()
        assert any(term in err_msg for term in ["denied", "read only", "command denied", "1142", "1792"])


@pytest.mark.integration
def test_readonly_user_drop_fails(readonly_connection):
    """Proves chatbot_readonly is blocked from dropping tables."""
    with readonly_connection.cursor() as cursor:
        with pytest.raises(Exception) as exc:
            cursor.execute("DROP TABLE products;")
        err_msg = str(exc.value).lower()
        assert any(term in err_msg for term in ["denied", "read only", "command denied", "1142", "1792"])


@pytest.mark.integration
def test_readonly_user_select_on_users_fails_1142(readonly_connection):
    """
    Proves chatbot_readonly cannot access internal security table 'users'.
    Must fail specifically with MySQL Error 1142 (SELECT command denied to user for table 'users').
    """
    with readonly_connection.cursor() as cursor:
        with pytest.raises(Exception) as exc:
            cursor.execute("SELECT * FROM users;")
        err_str = str(exc.value)
        # MySQL Error 1142: SELECT command denied to user ... for table 'users'
        assert "1142" in err_str or "denied" in err_str.lower()
        assert "users" in err_str.lower()


@pytest.mark.integration
def test_readonly_timeout_aborted(readonly_connection):
    """
    Proves that queries exceeding max_execution_time are automatically aborted by MySQL.
    """
    with readonly_connection.cursor() as cursor:
        # Set short timeout of 500ms for this test
        cursor.execute("SET SESSION max_execution_time=500;")
        with pytest.raises(Exception) as exc:
            cursor.execute("SELECT SLEEP(3);")
        err_str = str(exc.value).lower()
        # MySQL Error 3024 / 1317: Query execution was interrupted, max_execution_time exceeded
        assert any(t in err_str for t in ["interrupted", "max_execution_time", "3024", "1317"])


def test_connect_hook_fails_closed_on_cursor_error():
    """Proves that database.py connect hook raises (fails closed) if cursor execution fails."""
    from unittest.mock import MagicMock
    from src.database import set_readonly_session

    mock_cursor = MagicMock()
    mock_cursor.execute.side_effect = Exception("Simulated DB cursor failure during SET")

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    with pytest.raises(RuntimeError) as exc_info:
        set_readonly_session(mock_conn, None)

    assert "Query engine session hardening failed" in str(exc_info.value)
    assert "Simulated DB cursor failure" in str(exc_info.value)


def test_readonly_fixture_fails_in_ci_if_connection_unavailable(monkeypatch):
    """Proves that if CI is detected, integration tests fail rather than skipping when connection fails."""
    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr(settings, "DB_HOST", "invalid-ci-host-for-testing")
    monkeypatch.setattr(settings, "DB_PORT", 9999)

    with pytest.raises(pytest.fail.Exception) as exc_info:
        _connect_readonly()

    assert "Integration tests cannot be skipped in CI" in str(exc_info.value)


