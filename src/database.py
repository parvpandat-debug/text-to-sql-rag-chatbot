import os
from sqlalchemy import create_engine, text
from langchain_community.utilities import SQLDatabase
from dotenv import load_dotenv

load_dotenv()

user = os.getenv("DB_USER", "root")
pwd = os.getenv("DB_PASSWORD", "")
host = os.getenv("DB_HOST", "localhost")
port = os.getenv("DB_PORT", "3306")
db_name = os.getenv("DB_NAME", "text_to_sql")

DATABASE_URI = f"mysql+pymysql://{user}:{pwd}@{host}:{port}/{db_name}"
engine = create_engine(DATABASE_URI)

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
        DATABASE_URI,
        sample_rows_in_table_info=2,
        ignore_tables=["users", "chat_logs"]
    )