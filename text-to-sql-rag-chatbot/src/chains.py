import logging
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough

from src.config import settings
from src.security import SQLSecurityGuard, SQLSecurityError

logger = logging.getLogger(__name__)

def get_llm():
    if not settings.GOOGLE_API_KEY:
        raise RuntimeError("GOOGLE_API_KEY is not set in environment or secret manager.")
    return ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=settings.GOOGLE_API_KEY,
        temperature=0
    )

def build_sql_chain(db, llm):
    template = """Based on the table schema below, write a syntactically correct read-only MySQL SELECT query that answers the user's question.
Strict Security Rules:
- Only generate SELECT queries. Never generate INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, or schema-altering commands.
- Never query internal tables such as `users` or `chat_logs`.
- Return only the raw SQL query on a single line. Do not include markdown code blocks or explanations.

Schema:
{schema}

Question: {question}
SQL Query:"""
    
    prompt = ChatPromptTemplate.from_template(template)
    
    return (
        RunnablePassthrough.assign(schema=lambda _: db.get_table_info())
        | prompt
        | llm.bind(stop=["\nSQLResult:"])
        | StrOutputParser()
    )

def build_full_chain(db, llm, sql_chain):
    answer_template = """Given the user question, corresponding SQL query, and SQL result, answer the user question accurately in plain English.
If the SQL result indicates a security policy rejection or error, explain politely to the user what policy was violated.

Question: {question}
SQL Query: {query}
SQL Result: {result}
Answer:"""
    
    answer_prompt = ChatPromptTemplate.from_template(answer_template)

    # Initialize SQL Security Guard with allowed business tables from the database
    allowed_tables = set(db.get_usable_table_names())
    guard = SQLSecurityGuard(
        allowed_tables=allowed_tables,
        max_limit=settings.MAX_SQL_ROW_LIMIT,
        max_result_chars=settings.MAX_RESULT_CHARS
    )

    def execute_safely(raw_query: str) -> str:
        try:
            # 1. AST Validation & Limit Clamping (Hard Gate)
            sanitized_query, tables = guard.validate_and_sanitize(raw_query)
            logger.info(f"[SQL_GUARD] Executing sanitized query on tables {tables}: {sanitized_query}")
            
            # 2. Execute on Database
            raw_result = db.run(sanitized_query)
            
            # 3. Truncate Result if Exceeds Character Budget
            return guard.truncate_result(str(raw_result))
        except SQLSecurityError as sec_err:
            logger.warning(f"[SECURITY_ALERT] Blocked query attempt: {raw_query} | Reason: {sec_err}")
            return f"Security Policy Rejection: {str(sec_err)}. Query execution was blocked."
        except Exception as e:
            logger.error(f"[DB_ERROR] Query execution error: {e}")
            return f"Database execution error: {str(e)}"

    def get_or_generate_query(x):
        if "query" in x and x["query"]:
            return x["query"]
        return sql_chain.invoke(x)

    return (
        RunnablePassthrough.assign(query=get_or_generate_query)
        .assign(result=lambda x: execute_safely(x["query"]))
        | answer_prompt
        | llm
        | StrOutputParser()
    )