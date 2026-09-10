import logging
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any

import jwt
from fastapi import FastAPI, HTTPException, Depends, Request, Response, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from langchain_core.messages import HumanMessage, AIMessage

from src.config import settings
from src.database import get_db, engine
from src.chains import get_llm, build_sql_chain, build_full_chain
from src.auth import (
    hash_password,
    verify_password,
    create_access_token,
    get_current_user
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s"
)
logger = logging.getLogger("server")

sql_chain = None
full_chain = None

def get_user_rate_limit_key(request: Request) -> str:
    """Extracts authenticated user id from cookie or bearer token for rate limiting; falls back to IP."""
    token: Optional[str] = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1]
    if token and settings.JWT_SECRET:
        try:
            payload = jwt.decode(
                token,
                settings.JWT_SECRET,
                algorithms=["HS256"],
                options={"verify_exp": False}
            )
            sub = payload.get("sub")
            if sub:
                return f"user:{sub}"
        except Exception:
            pass
    return get_remote_address(request)

# Rate limiter setup (Phase 1, item 4)
limiter = Limiter(key_func=get_user_rate_limit_key)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global sql_chain, full_chain
    # Fail loudly if required secrets or credentials are unset (Phase 1, item 2)
    logger.info("[BOOT] Validating startup secrets and configuration...")
    settings.validate_boot_secrets()

    try:
        db = get_db()
        llm = get_llm()
        sql_chain = build_sql_chain(db, llm)
        full_chain = build_full_chain(db, llm, sql_chain)
        logger.info("[INIT] Text-to-SQL Enterprise RAG Service initialized successfully.")
    except Exception as e:
        logger.error(f"[ERROR] Startup failure: {e}", exc_info=True)
        raise
    yield

app = FastAPI(
    title="Enterprise Text-to-SQL API",
    description="Production-hardened Text-to-SQL RAG Chatbot with AST SQL Guard and httpOnly auth",
    version="2.0.0",
    lifespan=lifespan
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Input models with strict bounds (Phase 2, item 12)
class AuthRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_\-\.]+$")
    password: str = Field(min_length=8, max_length=128)

class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/health")
def health_check():
    """Liveness & readiness probe endpoint for container orchestrators."""
    return {
        "status": "healthy",
        "environment": settings.ENVIRONMENT
    }

@app.get("/")
def serve_index():
    return FileResponse("static/index.html")

# --- AUTH ENDPOINTS ---

@app.post("/api/auth/register")
def register(req: AuthRequest, response: Response):
    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT id FROM users WHERE username = :u"), {"u": req.username}
        ).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail="Username already exists.")
        
        hashed = hash_password(req.password)
        result = conn.execute(
            text("INSERT INTO users (username, hashed_password) VALUES (:u, :p)"),
            {"u": req.username, "p": hashed}
        )
        token = create_access_token(result.lastrowid, req.username)

        # Set secure httpOnly cookie (Phase 1, item 7)
        response.set_cookie(
            key="access_token",
            value=token,
            httponly=True,
            secure=settings.COOKIE_SECURE,
            samesite="lax",
            max_age=7 * 24 * 3600,
            path="/"
        )
        return {"token": token, "username": req.username, "message": "Account created successfully."}

@app.post("/api/auth/login")
def login(req: AuthRequest, response: Response):
    with engine.connect() as conn:
        user_record = conn.execute(
            text("SELECT id, username, hashed_password FROM users WHERE username = :u"),
            {"u": req.username}
        ).fetchone()

        if not user_record or not verify_password(req.password, user_record.hashed_password):
            raise HTTPException(status_code=401, detail="Invalid username or password.")

        token = create_access_token(user_record.id, user_record.username)

        # Set secure httpOnly cookie (Phase 1, item 7)
        response.set_cookie(
            key="access_token",
            value=token,
            httponly=True,
            secure=settings.COOKIE_SECURE,
            samesite="lax",
            max_age=7 * 24 * 3600,
            path="/"
        )
        return {"token": token, "username": user_record.username, "message": "Logged in successfully."}

@app.post("/api/auth/logout")
def logout(response: Response):
    """Clears the authentication cookie."""
    response.delete_cookie(key="access_token", path="/")
    return {"message": "Logged out successfully."}

@app.get("/api/auth/me")
def get_current_user_profile(user: dict = Depends(get_current_user)):
    """Verifies session and returns current user identity."""
    return {"id": user["id"], "username": user["username"]}

# --- MEMORY & CHAT ENDPOINTS ---

@app.get("/api/history")
def get_long_term_history(user: dict = Depends(get_current_user)):
    """Long-Term Memory: Fetches all historical queries for the authenticated user."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT question, generated_sql, answer, created_at 
                FROM chat_logs 
                WHERE user_id = :uid 
                ORDER BY created_at ASC
            """),
            {"uid": user["id"]}
        ).fetchall()

        history = [
            {
                "question": r.question,
                "generated_sql": r.generated_sql,
                "answer": r.answer,
                "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S")
            }
            for r in rows
        ]
        return {"history": history, "username": user["username"]}

@app.post("/api/chat")
@limiter.limit(settings.RATE_LIMIT_CHAT)
def handle_chat(
    request: Request,
    req: QueryRequest,
    user: dict = Depends(get_current_user)
):
    """Chat endpoint with per-user rate limiting and SQL safety enforcement."""
    clean_question = req.question.strip()
    if not clean_question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    # 1. Short-Term Memory: Fetch last 3 turns
    with engine.connect() as conn:
        recent_rows = conn.execute(
            text("""
                SELECT question, answer 
                FROM chat_logs 
                WHERE user_id = :uid 
                ORDER BY created_at DESC 
                LIMIT 3
            """),
            {"uid": user["id"]}
        ).fetchall()

    short_term_history = []
    for r in reversed(recent_rows):
        short_term_history.append(HumanMessage(content=r.question))
        short_term_history.append(AIMessage(content=r.answer))

    try:
        # 2. Invoke chain with short-term conversational context
        payload = {"question": clean_question, "chat_history": short_term_history}
        generated_sql = sql_chain.invoke(payload)
        payload["query"] = generated_sql
        final_answer = full_chain.invoke(payload)

        # 3. Long-Term Memory: Persist question, query, and result into database
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO chat_logs (user_id, question, generated_sql, answer) 
                    VALUES (:uid, :q, :sql, :ans)
                """),
                {
                    "uid": user["id"],
                    "q": clean_question,
                    "sql": str(generated_sql),
                    "ans": str(final_answer)
                }
            )

        return {"query": str(generated_sql), "answer": str(final_answer)}
    except Exception as e:
        logger.error(f"Chat processing error for user {user['id']}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Execution error: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)