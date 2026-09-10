import logging
from sqlalchemy import create_engine, text
from langchain_community.utilities import SQLDatabase
from src.config import settings

logger = logging.getLogger(__name__)

# Connection pooling settings (Phase 2, item 8):
# pool_size: number of persistent connections to maintain
# max_overflow: max additional burst connections
# pool_recycle: 3600s (reconnect before MySQL default 8hr wait_timeout kills connection)
# pool_pre_ping: test connection with a lightweight SELECT 1 before checkout
engine = create_engine(
    settings.DATABASE_URI,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_pre_ping=settings.DB_POOL_PRE_PING
)

def init_auth_and_memory_tables():
    """Initializes tables for persistent user auth and long-term memory."""
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                hashed_password VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS chat_logs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                question TEXT NOT NULL,
                generated_sql TEXT,
                answer TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """))

def get_db():
    init_auth_and_memory_tables()
    # Exclude users and chat_logs from schema introspection so Gemini only queries business data
    return SQLDatabase.from_uri(
        settings.DATABASE_URI,
        sample_rows_in_table_info=2,
        ignore_tables=["users", "chat_logs"]
    )