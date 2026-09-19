"""
FastAPI Asynchronous Enterprise Server for Text-to-SQL RAG Chatbot.

Features:
1. Real async def endpoints using aiomysql async connection pooling.
2. Dual database engines: App Engine (read/write) for auth & memory, Query Engine (read-only) for SQL execution.
3. RAG pipeline with ChromaDB vector schema retrieval and Gemini embeddings.
4. AST SQL Security Guard with row-limit clamping (<=50) and table-level isolation.
5. EXPLAIN plan safety checks and self-correction retry loop.
6. Multi-turn conversational memory (last 3 turns via MessagesPlaceholder).
7. Connection pool diagnostics on GET /api/health.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any

from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy import text
from langchain_core.messages import HumanMessage, AIMessage

from src.config import settings
from src.database import (
    app_engine,
    query_engine,
    get_db_util,
    get_introspected_business_tables,
    get_introspected_schema_dict,
    get_pool_status,
    init_auth_and_memory_tables
)
from src.sql_guard import SQLSecurityGuard, SQLGuardError
from src.rag import SchemaRAGIndex
from src.chains import get_llm, execute_rag_pipeline
from src.auth import (
    AuthRequest,
    QueryRequest,
    hash_password,
    verify_password,
    create_access_token,
    get_current_user
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("server")

# Global singleton runtime instances
guard: Optional[SQLSecurityGuard] = None
rag_index: Optional[SchemaRAGIndex] = None
llm = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initializes schema introspection, AST guard allowlists, and RAG vector store on startup."""
    global guard, rag_index, llm
    logger.info("[STARTUP] Initializing Text-to-SQL Enterprise RAG Backend...")

    try:
        # 1. Run DB table migration and introspection in worker thread to avoid blocking event loop
        db_util = await asyncio.to_thread(get_db_util)
        business_tables = await asyncio.to_thread(get_introspected_business_tables)
        await asyncio.to_thread(get_introspected_schema_dict)
        logger.info(f"[SCHEMA] Introspected business tables: {business_tables}")

        # 2. Initialize AST Security Guard with introspected table allowlist
        guard = SQLSecurityGuard(
            allowed_tables=set(business_tables),
            max_rows=settings.MAX_SQL_ROW_LIMIT,
            max_result_chars=settings.MAX_RESULT_CHARS
        )

        # 3. Initialize RAG Vector Index
        rag_index = SchemaRAGIndex()
        await asyncio.to_thread(rag_index.initialize_or_refresh_index, db_util)

        # 4. Initialize LLM
        llm = get_llm()
        logger.info("[STARTUP] Async RAG Chatbot with AST Guard and Connection Pooling ready.")
    except Exception as e:
        # If GOOGLE_API_KEY is configured and indexing fails, fail startup loudly (no silent fallback)
        if settings.GOOGLE_API_KEY and settings.GOOGLE_API_KEY.strip():
            logger.error(f"[STARTUP ERROR] Startup failed with GOOGLE_API_KEY configured: {e}")
            raise
        logger.warning(f"[STARTUP WARNING] Full database startup could not be completed ({e}). Initializing fallback guard with deterministic embeddings.")
        fallback_tables = {"customers", "products", "orders", "order_items"}
        guard = SQLSecurityGuard(allowed_tables=fallback_tables, max_rows=50)
        rag_index = SchemaRAGIndex()
        llm = get_llm()

    yield
    logger.info("[SHUTDOWN] Closing database engine pools...")
    await app_engine.dispose()
    await query_engine.dispose()


app = FastAPI(
    title="Enterprise Text-to-SQL RAG API",
    description="Production-grade Text-to-SQL RAG Pipeline with AST Security Guard, aiomysql pooling, and Gemini",
    version="2.0.0",
    lifespan=lifespan
)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def serve_index():
    """Serves the static web UI."""
    return FileResponse("static/index.html")


@app.get("/api/health")
async def health_check():
    """
    Exposes system health, database connectivity, and connection pool utilization
    for both the App Engine and the Read-Only Query Engine.
    """
    pool_stats = get_pool_status()
    db_connected = True
    try:
        async with app_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        db_connected = False

    return {
        "status": "healthy" if db_connected else "degraded",
        "database_connected": db_connected,
        "environment": settings.ENVIRONMENT,
        "row_limit_clamped": settings.MAX_SQL_ROW_LIMIT,
        "connection_pools": pool_stats
    }


# --- AUTHENTICATION ENDPOINTS ---

@app.post("/api/auth/register")
async def register(req: AuthRequest):
    """Registers a new user with bcrypt password hashing."""
    async with app_engine.begin() as conn:
        existing = await conn.execute(
            text("SELECT id FROM users WHERE username = :u"),
            {"u": req.username}
        )
        if existing.fetchone():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username already exists.")

        hashed = hash_password(req.password)
        result = await conn.execute(
            text("INSERT INTO users (username, hashed_password) VALUES (:u, :p)"),
            {"u": req.username, "p": hashed}
        )
        user_id = result.lastrowid
        token = create_access_token(user_id, req.username)
        return {"token": token, "username": req.username}


@app.post("/api/auth/login")
async def login(req: AuthRequest):
    """Authenticates existing user credentials and returns signed JWT."""
    async with app_engine.connect() as conn:
        result = await conn.execute(
            text("SELECT id, username, hashed_password FROM users WHERE username = :u"),
            {"u": req.username}
        )
        user_record = result.fetchone()

        if not user_record or not verify_password(req.password, user_record.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password."
            )

        token = create_access_token(user_record.id, user_record.username)
        return {"token": token, "username": user_record.username}


# --- MEMORY & CHAT ENDPOINTS ---

@app.get("/api/history")
async def get_chat_history(user: dict = Depends(get_current_user)):
    """Long-Term Memory: Fetches persistent chat history strictly isolated to the authenticated user."""
    async with app_engine.connect() as conn:
        result = await conn.execute(
            text("""
                SELECT question, generated_sql, answer, created_at 
                FROM chat_logs 
                WHERE user_id = :uid 
                ORDER BY created_at ASC
            """),
            {"uid": user["id"]}
        )
        rows = result.fetchall()

    history = [
        {
            "question": r.question,
            "generated_sql": r.generated_sql,
            "answer": r.answer,
            "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if hasattr(r.created_at, "strftime") else str(r.created_at)
        }
        for r in rows
    ]
    return {"history": history, "username": user["username"]}


@app.post("/api/chat")
async def handle_chat(req: QueryRequest, user: dict = Depends(get_current_user)):
    """
    Asynchronous RAG Chatbot Pipeline:
    1. Short-Term Memory: fetches last 3 conversational turns.
    2. Real RAG: schema & few-shot example retrieval via ChromaDB.
    3. Single SQL generation + AST Security Guard + LIMIT clamping.
    4. Execution Plan EXPLAIN analysis.
    5. Read-only query execution with self-correction retry loop.
    6. Persists question and SANITIZED SQL into MySQL long-term memory.
    """
    if not req.question or not req.question.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Question cannot be empty.")

    # 1. Short-Term Memory: Retrieve last 3 turns (up to 6 messages) for conversational follow-ups
    async with app_engine.connect() as conn:
        recent_result = await conn.execute(
            text("""
                SELECT question, answer 
                FROM chat_logs 
                WHERE user_id = :uid 
                ORDER BY created_at DESC 
                LIMIT 3
            """),
            {"uid": user["id"]}
        )
        recent_rows = recent_result.fetchall()

    short_term_history = []
    for r in reversed(recent_rows):
        short_term_history.append(HumanMessage(content=r.question))
        short_term_history.append(AIMessage(content=r.answer))

    try:
        # 2. Execute Complete RAG Pipeline with Guard & Self-Correction
        pipeline_output = await execute_rag_pipeline(
            question=req.question.strip(),
            chat_history=short_term_history,
            rag_index=rag_index,
            guard=guard,
            llm=llm
        )

        sanitized_sql = pipeline_output["query"]
        final_answer = pipeline_output["answer"]

        # 3. Long-Term Memory: Persist question, SANITIZED SQL, and answer
        async with app_engine.begin() as conn:
            await conn.execute(
                text("""
                    INSERT INTO chat_logs (user_id, question, generated_sql, answer) 
                    VALUES (:uid, :q, :sql, :ans)
                """),
                {
                    "uid": user["id"],
                    "q": req.question.strip(),
                    "sql": str(sanitized_sql),
                    "ans": str(final_answer)
                }
            )

        return {
            "query": str(sanitized_sql),
            "answer": str(final_answer)
        }

    except SQLGuardError as ge:
        # Security violation (rejected by AST guard)
        logger.warning(f"Guard blocked query for user {user['username']}: {ge}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Security Guard blocked query: {ge.message}"
        )
    except Exception as e:
        logger.error(f"Execution error processing chat: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Execution error: {str(e)}"
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)