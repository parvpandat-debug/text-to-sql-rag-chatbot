"""
Integration Tests for FastAPI API Endpoints.

Covers:
1. GET /api/health (database connectivity and connection pool metrics).
2. POST /api/auth/register and POST /api/auth/login (registration, duplicate rejection, login).
3. POST /api/chat (empty question rejection, happy path with mocked LLM).
4. Malicious LLM output blocked (preventing prompt-injection DDL execution).
5. User chat history isolation (User B cannot see User A's logs).
6. Multi-turn conversational follow-up utilizing short-term history.
"""

import pytest
from sqlalchemy import text
from langchain_community.chat_models.fake import FakeListChatModel
import server as srv_mod


# --- 1. System Health & Diagnostics ---

@pytest.mark.asyncio
async def test_api_health(client):
    res = await client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert "connection_pools" in data
    assert "row_limit_clamped" in data
    assert data["row_limit_clamped"] == 50


# --- 2. Auth Endpoints ---

@pytest.mark.asyncio
async def test_register_and_duplicate_rejection(client):
    payload = {"username": "new_engineer_99", "password": "SecurePassword123!"}

    # First registration should succeed
    res = await client.post("/api/auth/register", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert "token" in data
    assert data["username"] == "new_engineer_99"

    # Duplicate registration must fail with 400
    res_dup = await client.post("/api/auth/register", json=payload)
    assert res_dup.status_code == 400
    assert "already exists" in res_dup.json()["detail"].lower()


@pytest.mark.asyncio
async def test_login_success_and_failure(client):
    user_cred = {"username": "login_user_1", "password": "ValidPassword123!"}
    await client.post("/api/auth/register", json=user_cred)

    # Valid login
    res = await client.post("/api/auth/login", json=user_cred)
    assert res.status_code == 200
    assert "token" in res.json()

    # Invalid password login
    res_bad = await client.post("/api/auth/login", json={"username": "login_user_1", "password": "WrongPassword123!"})
    assert res_bad.status_code == 401
    assert "Invalid username or password" in res_bad.json()["detail"]


# --- 3. Chat & Guard Endpoints ---

@pytest.mark.asyncio
async def test_chat_empty_question_rejected(client, authenticated_user):
    # Empty question
    res = await client.post(
        "/api/chat",
        json={"question": ""},
        headers=authenticated_user["headers"]
    )
    assert res.status_code in [400, 422]


@pytest.mark.asyncio
async def test_chat_happy_path(client, authenticated_user, monkeypatch, test_db_engine):
    # Mock LLM to return valid candidate SQL, then natural language answer
    mock_llm = FakeListChatModel(responses=[
        "SELECT id, name, price FROM products WHERE price > 50;",  # SQL generation
        "We have the Laptop product which costs $1200."             # Answer synthesis
    ])
    monkeypatch.setattr(srv_mod, "llm", mock_llm)

    # Mock execute_query_readonly to return row dicts for SQLite test engine
    async def mock_execute(sql: str):
        async with test_db_engine.connect() as conn:
            res = await conn.execute(text(sql))
            keys = list(res.keys())
            return [dict(zip(keys, r)) for r in res.fetchall()]

    monkeypatch.setattr("src.chains.execute_query_readonly", mock_execute)

    res = await client.post(
        "/api/chat",
        json={"question": "What products cost more than 50 dollars?"},
        headers=authenticated_user["headers"]
    )

    assert res.status_code == 200
    data = res.json()
    assert "query" in data
    assert "LIMIT 50" in data["query"]
    assert "products" in data["query"].lower()
    assert "Laptop" in data["answer"]


@pytest.mark.asyncio
async def test_chat_blocks_malicious_llm_output(client, authenticated_user, monkeypatch):
    # Simulate LLM prompt injection attack: LLM outputs DROP TABLE customers
    malicious_llm = FakeListChatModel(responses=[
        "DROP TABLE customers;"
    ])
    monkeypatch.setattr(srv_mod, "llm", malicious_llm)

    res = await client.post(
        "/api/chat",
        json={"question": "Delete all data!"},
        headers=authenticated_user["headers"]
    )

    # Must be blocked by AST Security Guard with 400 Bad Request
    assert res.status_code == 400
    assert "Security Guard blocked query" in res.json()["detail"]


@pytest.mark.asyncio
async def test_history_isolation_between_two_users(client, authenticated_user, test_db_engine):
    # 1. Insert chat log for User A (authenticated_user)
    async with test_db_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO chat_logs (user_id, question, generated_sql, answer) VALUES (:uid, 'User A Question', 'SELECT 1;', 'User A Answer')"),
            {"uid": authenticated_user["id"]}
        )

    # Verify User A can see their log
    res_a = await client.get("/api/history", headers=authenticated_user["headers"])
    assert res_a.status_code == 200
    assert len(res_a.json()["history"]) == 1
    assert res_a.json()["history"][0]["question"] == "User A Question"

    # 2. Register User B
    reg_b = await client.post("/api/auth/register", json={"username": "user_b_isolated", "password": "Password123!"})
    token_b = reg_b.json()["token"]
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # 3. User B checks their history: MUST BE EMPTY
    res_b = await client.get("/api/history", headers=headers_b)
    assert res_b.status_code == 200
    assert len(res_b.json()["history"]) == 0


# --- 4. Missing Token & Follow-Up Context Tests ---

@pytest.mark.asyncio
async def test_missing_token_returns_401(client):
    # Missing Authorization header on protected GET endpoint
    res_hist = await client.get("/api/history")
    assert res_hist.status_code in [401, 403]

    # Missing Authorization header on protected POST endpoint
    res_chat = await client.post("/api/chat", json={"question": "Show products"})
    assert res_chat.status_code in [401, 403]


@pytest.mark.asyncio
async def test_chat_follow_up_uses_history(client, authenticated_user, test_db_engine, monkeypatch):
    """Proves that follow-up questions load and pass previous conversation turns as chat_history."""
    # 1. Seed prior turn in chat_logs
    async with test_db_engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO chat_logs (user_id, question, generated_sql, answer)
                VALUES (:uid, 'What products are in Electronics?', 'SELECT * FROM products;', 'We have Laptop and Mouse.')
            """),
            {"uid": authenticated_user["id"]}
        )

    # 2. Track chat_history passed into execute_rag_pipeline
    captured_history = []

    async def mock_rag_pipeline(question, chat_history, rag_index, guard, llm, max_retries=2):
        captured_history.extend(chat_history)
        return {
            "query": "SELECT name, price FROM products ORDER BY price DESC LIMIT 1;",
            "answer": "Laptop is the most expensive product at $1200.",
            "rows_returned": 1,
            "retries": 0
        }

    monkeypatch.setattr("server.execute_rag_pipeline", mock_rag_pipeline)

    # 3. Send follow-up question
    res = await client.post(
        "/api/chat",
        json={"question": "Which of those is the most expensive?"},
        headers=authenticated_user["headers"]
    )

    assert res.status_code == 200
    assert len(captured_history) == 2, "Must pass the previous turn (HumanMessage and AIMessage) in chat_history!"
    assert captured_history[0].content == "What products are in Electronics?"
    assert captured_history[1].content == "We have Laptop and Mouse."


@pytest.mark.asyncio
async def test_lifespan_loud_failure_when_google_api_key_set(monkeypatch):
    """Proves that if GOOGLE_API_KEY is configured and RAG indexing fails, startup fails loudly."""
    from src.config import settings
    monkeypatch.setattr(settings, "GOOGLE_API_KEY", "AIzaSyFakeKeyForTest")

    # Mock DB introspection so it reaches RAG indexing
    dummy_db_util = object()
    monkeypatch.setattr(srv_mod, "get_db_util", lambda: dummy_db_util)
    monkeypatch.setattr("src.database.get_db_util", lambda: dummy_db_util)
    monkeypatch.setattr(srv_mod, "get_introspected_business_tables", lambda: ["customers"])
    monkeypatch.setattr("src.database.get_introspected_business_tables", lambda: ["customers"])

    def failing_init(self, db_util):
        raise RuntimeError("Google API quota exhausted / embedding endpoint down")

    monkeypatch.setattr("src.rag.SchemaRAGIndex.initialize_or_refresh_index", failing_init)

    with pytest.raises(RuntimeError) as exc_info:
        async with srv_mod.lifespan(srv_mod.app):
            pass

    assert "Google API quota exhausted" in str(exc_info.value)


@pytest.mark.asyncio
async def test_lifespan_fallback_when_no_google_api_key(monkeypatch):
    """Proves that when GOOGLE_API_KEY is not set, startup falls back to deterministic embeddings."""
    from src.config import settings
    from src.rag import DeterministicEmbeddings
    monkeypatch.setattr(settings, "GOOGLE_API_KEY", "")

    def failing_init():
        raise RuntimeError("Database not reachable")

    monkeypatch.setattr(srv_mod, "get_db_util", failing_init)
    monkeypatch.setattr("src.database.get_db_util", failing_init)

    async with srv_mod.lifespan(srv_mod.app):
        assert srv_mod.rag_index is not None
        assert isinstance(srv_mod.rag_index.embeddings, DeterministicEmbeddings)


