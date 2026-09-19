"""
Pytest Test Configuration and Fixtures.

Provides:
1. In-memory SQLite async engines for isolated, deterministic offline testing.
2. Mocked LLMs (FakeListChatModel) and deterministic embeddings.
3. Asynchronous HTTP test client (httpx.AsyncClient with ASGITransport).
4. Pre-authenticated user sessions for API endpoint testing.
"""

import pytest
import asyncio
from typing import AsyncGenerator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from httpx import AsyncClient, ASGITransport
from langchain_community.chat_models.fake import FakeListChatModel

from server import app
from src.config import settings
from src.auth import create_access_token, hash_password
from src.sql_guard import SQLSecurityGuard
from src.rag import SchemaRAGIndex, DeterministicEmbeddings


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for each test case."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def test_db_engine() -> AsyncGenerator[AsyncEngine, None]:
    """Creates an isolated in-memory async SQLite engine populated with schema."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    
    async with engine.begin() as conn:
        # App tables
        await conn.execute(text("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username VARCHAR(50) UNIQUE NOT NULL,
                hashed_password VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))
        await conn.execute(text("""
            CREATE TABLE chat_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                question TEXT NOT NULL,
                generated_sql TEXT,
                answer TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))
        # Business tables
        await conn.execute(text("""
            CREATE TABLE customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR(100) NOT NULL,
                email VARCHAR(100) NOT NULL,
                city VARCHAR(50)
            );
        """))
        await conn.execute(text("""
            CREATE TABLE products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR(100) NOT NULL,
                price REAL NOT NULL,
                category VARCHAR(50)
            );
        """))
        await conn.execute(text("""
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL,
                total_amount REAL NOT NULL,
                status VARCHAR(20) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))

        # Seed sample business rows
        await conn.execute(text("INSERT INTO customers (name, email, city) VALUES ('Alice', 'alice@test.com', 'New York'), ('Bob', 'bob@test.com', 'San Francisco');"))
        await conn.execute(text("INSERT INTO products (name, price, category) VALUES ('Laptop', 1200.0, 'Electronics'), ('Mouse', 25.0, 'Electronics');"))
        await conn.execute(text("INSERT INTO orders (customer_id, total_amount, status) VALUES (1, 1225.0, 'completed'), (2, 25.0, 'pending');"))

    yield engine
    await engine.dispose()


@pytest.fixture
def mock_guard() -> SQLSecurityGuard:
    """Returns a security guard configured with business tables allowlist."""
    return SQLSecurityGuard(
        allowed_tables={"customers", "products", "orders", "order_items"},
        max_rows=50,
        max_result_chars=2000
    )


@pytest.fixture
async def client(monkeypatch, test_db_engine, mock_guard) -> AsyncGenerator[AsyncClient, None]:
    """Provides an authenticated httpx.AsyncClient hooked to the FastAPI app with test db."""
    import src.database as db_mod
    import server as srv_mod

    # Patch database engines in both modules
    monkeypatch.setattr(db_mod, "app_engine", test_db_engine)
    monkeypatch.setattr(db_mod, "query_engine", test_db_engine)
    monkeypatch.setattr(srv_mod, "app_engine", test_db_engine)
    monkeypatch.setattr(srv_mod, "query_engine", test_db_engine)
    monkeypatch.setattr(srv_mod, "guard", mock_guard)

    # Initialize mock RAG index with deterministic embeddings
    rag = SchemaRAGIndex(persist_directory="data/test_chroma")
    monkeypatch.setattr(srv_mod, "rag_index", rag)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def authenticated_user(test_db_engine) -> dict:
    """Creates a test user directly in the database and returns user details and valid JWT token."""
    username = "test_engineer"
    password = "SuperSecretPassword123!"
    hashed = hash_password(password)

    async with test_db_engine.begin() as conn:
        res = await conn.execute(
            text("INSERT INTO users (username, hashed_password) VALUES (:u, :p)"),
            {"u": username, "p": hashed}
        )
        user_id = res.lastrowid

    token = create_access_token(user_id=user_id, username=username)
    return {
        "id": user_id,
        "username": username,
        "password": password,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"}
    }
