import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env if present (for local development)
load_dotenv()

def read_secret(var_name: str, default: Optional[str] = None) -> Optional[str]:
    """Reads a secret from an environment variable or Docker/K8s secret file."""
    # Check if a _FILE path is specified (standard Docker/K8s secret pattern)
    file_path = os.getenv(f"{var_name}_FILE")
    if file_path and Path(file_path).is_file():
        return Path(file_path).read_text().strip()
    
    # Check standard container secret directory /run/secrets/
    default_secret_path = Path(f"/run/secrets/{var_name.lower()}")
    if default_secret_path.is_file():
        return default_secret_path.read_text().strip()

    return os.getenv(var_name, default)

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    ENVIRONMENT: str = Field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))

    # Required Secrets - No silent defaults
    JWT_SECRET: str = Field(default_factory=lambda: read_secret("JWT_SECRET") or "")
    GOOGLE_API_KEY: str = Field(default_factory=lambda: read_secret("GOOGLE_API_KEY") or "")
    
    # Database Configuration - Required
    DB_USER: str = Field(default_factory=lambda: read_secret("DB_USER") or "")
    DB_PASSWORD: str = Field(default_factory=lambda: read_secret("DB_PASSWORD") or "")
    DB_HOST: str = Field(default_factory=lambda: os.getenv("DB_HOST", "localhost"))
    DB_PORT: int = Field(default_factory=lambda: int(os.getenv("DB_PORT", "3306")))
    DB_NAME: str = Field(default_factory=lambda: os.getenv("DB_NAME", ""))

    # Pool & Performance Settings
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_RECYCLE: int = 3600
    DB_POOL_PRE_PING: bool = True

    # Security & Guardrails
    MAX_SQL_ROW_LIMIT: int = 50
    MAX_RESULT_CHARS: int = 4000
    RATE_LIMIT_CHAT: str = "20/minute"

    @property
    def COOKIE_SECURE(self) -> bool:
        """Enforce HTTPS cookie in production and staging; allow HTTP in development."""
        return self.ENVIRONMENT.lower() in ("production", "staging")

    @property
    def DATABASE_URI(self) -> str:
        return f"mysql+pymysql://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    def validate_boot_secrets(self):
        """Strict validation at startup. Fails loudly with a comprehensive error message."""
        missing = []
        if not self.JWT_SECRET:
            missing.append("JWT_SECRET (must be a secure random secret, e.g. minimum 32 characters)")
        elif len(self.JWT_SECRET) < 16:
            missing.append("JWT_SECRET is too short (must be at least 16 characters for security)")

        if not self.GOOGLE_API_KEY:
            missing.append("GOOGLE_API_KEY (required for Gemini LLM calls)")

        if not self.DB_USER:
            missing.append("DB_USER")
        if not self.DB_PASSWORD and self.ENVIRONMENT != "test":
            missing.append("DB_PASSWORD")
        if not self.DB_NAME:
            missing.append("DB_NAME")

        if missing:
            err_msg = (
                "\n============================================================\n"
                "CRITICAL SECURITY CONFIGURATION ERROR:\n"
                "The application failed to start due to missing or invalid secrets.\n"
                "Missing environment variables:\n"
                + "\n".join(f"  - {m}" for m in missing) +
                "\n\nPlease configure these in your environment, Kubernetes/Docker secrets,\n"
                "or local .env file before starting the service.\n"
                "============================================================\n"
            )
            raise RuntimeError(err_msg)

settings = Settings()
