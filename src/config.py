"""
Application Configuration and Settings.

Enforces strict environment validation, JWT secret strength,
connection pooling settings, and dual-engine credentials.
"""

import os
from typing import Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Production configuration loaded from environment variables and .env file."""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Environment
    ENVIRONMENT: str = Field(default="development")

    # LLM & Embedding Model Settings
    GOOGLE_API_KEY: Optional[str] = Field(default=None)
    GEMINI_MODEL: str = Field(default="gemini-2.5-flash", description="Google Gemini LLM model identifier")

    # JWT Authentication (Required - no default)
    JWT_SECRET: str = Field(
        ...,
        description="Must be set and at least 32 characters long."
    )
    JWT_ALGORITHM: str = Field(default="HS256")
    JWT_EXPIRATION_DAYS: int = Field(default=7)

    # Primary Database (Read/Write App Engine for users and chat_logs)
    DB_USER: str = Field(default="root")
    DB_PASSWORD: str = Field(default="")
    DB_HOST: str = Field(default="localhost")
    DB_PORT: int = Field(default=3306)
    DB_NAME: str = Field(default="text_to_sql")

    # Dedicated Read-Only Query User (Chatbot Query Engine - Required - no default)
    QUERY_DB_USER: str = Field(default="chatbot_readonly")
    QUERY_DB_PASSWORD: str = Field(
        ...,
        description="Required password for read-only database query user."
    )

    # Connection Pooling Configuration
    DB_POOL_SIZE: int = Field(default=10)
    DB_MAX_OVERFLOW: int = Field(default=20)
    DB_POOL_TIMEOUT: int = Field(default=30)
    DB_POOL_RECYCLE: int = Field(default=1800)
    DB_POOL_PRE_PING: bool = Field(default=True)

    # Query Engine Session Limits
    QUERY_MAX_EXECUTION_TIME_MS: int = Field(default=5000)

    # Guardrail and Optimization Bounds
    MAX_SQL_ROW_LIMIT: int = Field(default=50)
    MAX_RESULT_CHARS: int = Field(default=4000)
    MAX_EXPLAIN_ROWS: int = Field(default=10000)

    # Vector RAG Settings
    TOP_K_TABLES: int = Field(default=4)
    TOP_K_EXAMPLES: int = Field(default=3)
    CHROMA_PERSIST_DIR: str = Field(default="data/chroma_db")

    @field_validator("JWT_SECRET")
    @classmethod
    def validate_jwt_secret(cls, v: str) -> str:
        if not v or len(v.strip()) < 32:
            raise ValueError(
                f"JWT_SECRET is insecure or missing! Must be at least 32 characters long. Provided length: {len(v) if v else 0}."
            )
        return v.strip()

    @field_validator("QUERY_DB_PASSWORD")
    @classmethod
    def validate_query_db_password(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("QUERY_DB_PASSWORD is required and cannot be empty.")
        return v.strip()

    @property
    def sync_app_db_uri(self) -> str:
        """Sync connection URI for initial introspection / migrations."""
        pwd = f":{self.DB_PASSWORD}" if self.DB_PASSWORD else ""
        return f"mysql+pymysql://{self.DB_USER}{pwd}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    @property
    def async_app_db_uri(self) -> str:
        """Async connection URI for FastAPI app engine (users & chat_logs)."""
        pwd = f":{self.DB_PASSWORD}" if self.DB_PASSWORD else ""
        return f"mysql+aiomysql://{self.DB_USER}{pwd}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}?charset=utf8mb4"

    @property
    def async_query_db_uri(self) -> str:
        """Async connection URI for read-only query engine."""
        pwd = f":{self.QUERY_DB_PASSWORD}" if self.QUERY_DB_PASSWORD else ""
        return f"mysql+aiomysql://{self.QUERY_DB_USER}{pwd}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}?charset=utf8mb4"


# Global singleton settings
settings = Settings()
