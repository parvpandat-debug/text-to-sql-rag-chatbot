import pytest
from src.config import Settings, read_secret

def test_missing_jwt_secret_fails_validation():
    s = Settings(
        JWT_SECRET="",
        GOOGLE_API_KEY="valid-key",
        DB_USER="root",
        DB_PASSWORD="pwd",
        DB_NAME="test"
    )
    with pytest.raises(RuntimeError) as exc:
        s.validate_boot_secrets()
    assert "JWT_SECRET" in str(exc.value)

def test_short_jwt_secret_fails_validation():
    s = Settings(
        JWT_SECRET="short",
        GOOGLE_API_KEY="valid-key",
        DB_USER="root",
        DB_PASSWORD="pwd",
        DB_NAME="test"
    )
    with pytest.raises(RuntimeError) as exc:
        s.validate_boot_secrets()
    assert "too short" in str(exc.value)

def test_missing_google_api_key_fails_validation():
    s = Settings(
        JWT_SECRET="valid-secret-with-plenty-of-characters-long",
        GOOGLE_API_KEY="",
        DB_USER="root",
        DB_PASSWORD="pwd",
        DB_NAME="test"
    )
    with pytest.raises(RuntimeError) as exc:
        s.validate_boot_secrets()
    assert "GOOGLE_API_KEY" in str(exc.value)

def test_valid_secrets_pass_validation():
    s = Settings(
        JWT_SECRET="valid-secret-with-plenty-of-characters-long",
        GOOGLE_API_KEY="valid-api-key",
        DB_USER="root",
        DB_PASSWORD="pwd",
        DB_NAME="test_db"
    )
    # Should not raise
    s.validate_boot_secrets()

def test_cookie_secure_by_environment():
    dev_settings = Settings(ENVIRONMENT="development")
    assert dev_settings.COOKIE_SECURE is False

    prod_settings = Settings(ENVIRONMENT="production")
    assert prod_settings.COOKIE_SECURE is True
