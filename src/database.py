"""
Database Connection and Dual-Engine Management Module.

Implements architectural isolation:
1. App Engine (Async Read/Write): dedicated to user auth and chat_logs.
2. Query Engine (Async Read-Only): dedicated to AI-generated SQL execution, running under
   least-privilege credentials with session-level READ ONLY and max_execution_time enforcement.
3. Explicit connection pooling with pool statistics reporting for /api/health.
4. Schema introspection and migration helpers.
"""

import logging
from functools import lru_cache
from typing import Dict, Any, List, Optional
from sqlalchemy import create_engine, text, event
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from langchain_community.utilities import SQLDatabase

from src.config import settings

logger = logging.getLogger(__name__)

# --- App Engine (Read/Write for Users & Chat Logs) ---
app_engine: AsyncEngine = create_async_engine(
    settings.async_app_db_uri,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_pre_ping=settings.DB_POOL_PRE_PING,
    echo=False
)

# --- Query Engine (Strictly Read-Only with Session Hardening) ---
query_engine: AsyncEngine = create_async_engine(
    settings.async_query_db_uri,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_pre_ping=settings.DB_POOL_PRE_PING,
    echo=False
)


@event.listens_for(query_engine.sync_engine, "connect")
def set_readonly_session(dbapi_connection, connection_record):
    """
    Enforces read-only transaction and execution timeout on every connection.
    Executes each SET statement separately.
    """
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("SET SESSION TRANSACTION READ ONLY;")
        cursor.execute(f"SET SESSION max_execution_time={settings.QUERY_MAX_EXECUTION_TIME_MS};")
        cursor.close()
    except Exception as e:
        logger.error(f"[SECURITY] Query engine session hardening connect event failed: {e}")
        raise RuntimeError(f"Query engine session hardening failed: {e}") from e

# Synchronous engine for startup schema introspection and table creation
sync_app_engine = create_engine(
    settings.sync_app_db_uri,
    pool_pre_ping=settings.DB_POOL_PRE_PING
)


def init_auth_and_memory_tables() -> None:
    """Initializes tables for persistent user auth and long-term memory."""
    with sync_app_engine.begin() as conn:
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


def get_db_util() -> SQLDatabase:
    """Returns LangChain SQLDatabase utility for synchronous table info introspection."""
    init_auth_and_memory_tables()
    return SQLDatabase.from_uri(
        settings.sync_app_db_uri,
        sample_rows_in_table_info=2,
        ignore_tables=["users", "chat_logs"]
    )


def get_introspected_business_tables() -> List[str]:
    """Dynamically returns list of business tables available for querying."""
    try:
        db = get_db_util()
        return [t.lower() for t in db.get_usable_table_names()]
    except Exception as e:
        logger.warning(f"Failed to introspect tables dynamically: {e}")
        # Default fallback business tables if DB is not populated yet
        return ["customers", "products", "orders", "order_items"]


@lru_cache(maxsize=1)
def get_introspected_schema_dict() -> Dict[str, Dict[str, str]]:
    """
    Returns schema dictionary for sqlglot optimizer:
    { "table_name": { "column_name": "column_type" } }
    """
    try:
        from sqlalchemy import inspect
        inspector = inspect(sync_app_engine)
        schema_dict: Dict[str, Dict[str, str]] = {}
        for table_name in inspector.get_table_names():
            if table_name in ["users", "chat_logs"]:
                continue
            cols: Dict[str, str] = {}
            for col in inspector.get_columns(table_name):
                cols[col["name"]] = str(col["type"])
            schema_dict[table_name] = cols
        if schema_dict:
            return schema_dict
    except Exception as e:
        logger.debug(f"Could not introspect column types directly: {e}")

    # Fallback schema dictionary matching seeded business schema
    return {
        "customers": {"id": "int", "name": "varchar", "email": "varchar", "city": "varchar", "signup_date": "date"},
        "products": {"id": "int", "name": "varchar", "category": "varchar", "price": "decimal", "stock": "int"},
        "orders": {"id": "int", "customer_id": "int", "total_amount": "decimal", "status": "varchar", "order_date": "date"},
        "order_items": {"id": "int", "order_id": "int", "product_id": "int", "quantity": "int", "unit_price": "decimal"}
    }


def get_pool_status() -> Dict[str, Any]:
    """
    Extracts live connection pool metrics from both App and Query engines.
    Used by GET /api/health to expose connection health and pool utilization.
    """
    def _extract_stats(engine: AsyncEngine) -> Dict[str, Any]:
        pool = engine.sync_engine.pool
        try:
            return {
                "size": pool.size(),
                "checked_in": pool.checkedin(),
                "checked_out": pool.checkedout(),
                "overflow": pool.overflow(),
            }
        except Exception as e:
            return {"error": str(e)}

    return {
        "app_pool": _extract_stats(app_engine),
        "query_pool": _extract_stats(query_engine),
    }


async def execute_query_readonly(sanitized_sql: str) -> List[Dict[str, Any]]:
    """
    Executes sanitized SQL strictly through the read-only query engine.
    Also ensures transaction is explicitly read-only before running on MySQL.
    
    Returns:
        List of row dictionaries (column_name -> value).
    """
    async with query_engine.connect() as conn:
        # Extra safety check: ensure session is transaction read only on MySQL
        if hasattr(conn, "dialect") and getattr(conn.dialect, "name", "") == "mysql":
            await conn.execute(text("SET SESSION TRANSACTION READ ONLY;"))
        cursor = await conn.execute(text(sanitized_sql))
        keys = list(cursor.keys())
        rows = cursor.fetchall()
        return [dict(zip(keys, row)) for row in rows]