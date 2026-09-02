import os
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import text
from dotenv import load_dotenv

from langchain_core.messages import HumanMessage, AIMessage
from src.database import get_db, engine
from src.chains import get_llm, build_sql_chain, build_full_chain
from src.auth import (
    hash_password,
    verify_password,
    create_access_token,
    get_current_user
)

load_dotenv()

sql_chain = None
full_chain = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global sql_chain, full_chain
    try:
        db = get_db()
        llm = get_llm()
        sql_chain = build_sql_chain(db, llm)
        full_chain = build_full_chain(db, llm, sql_chain)
        print("[INIT] Production SQL Chatbot with Auth & Hybrid Memory ready.")
    except Exception as e:
        print(f"[ERROR] Startup failure: {e}")
    yield

app = FastAPI(title="Text-to-SQL Enterprise API", lifespan=lifespan)

class AuthRequest(BaseModel):
    username: str
    password: str

class QueryRequest(BaseModel):
    question: str

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def serve_index():
    return FileResponse("static/index.html")

# --- AUTH ENDPOINTS ---

@app.post("/api/auth/register")
def register(req: AuthRequest):
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
        return {"token": token, "username": req.username}

@app.post("/api/auth/login")
def login(req: AuthRequest):
    with engine.connect() as conn:
        user_record = conn.execute(
            text("SELECT id, username, hashed_password FROM users WHERE username = :u"),
            {"u": req.username}
        ).fetchone()

        if not user_record or not verify_password(req.password, user_record.hashed_password):
            raise HTTPException(status_code=401, detail="Invalid username or password.")

        token = create_access_token(user_record.id, user_record.username)
        return {"token": token, "username": user_record.username}

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
def handle_chat(req: QueryRequest, user: dict = Depends(get_current_user)):
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    # 1. Short-Term Memory: Fetch only the last 3 turns (6 messages) for runtime context
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
        payload = {"question": req.question, "chat_history": short_term_history}
        generated_sql = sql_chain.invoke(payload)
        payload["query"] = generated_sql
        final_answer = full_chain.invoke(payload)

        # 3. Long-Term Memory: Persist question, query, and result into MySQL
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO chat_logs (user_id, question, generated_sql, answer) 
                    VALUES (:uid, :q, :sql, :ans)
                """),
                {
                    "uid": user["id"],
                    "q": req.question,
                    "sql": str(generated_sql),
                    "ans": str(final_answer)
                }
            )

        return {"query": str(generated_sql), "answer": str(final_answer)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Execution error: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)