"""
LangChain Orchestration Module with Gemini, AST Guard, and Self-Correction.

Implements:
1. MessagesPlaceholder supporting conversational multi-turn context (last 3 turns).
2. Prompt engineering for optimized SQL (no SELECT *, explicit joins, aggregations, mandatory limit).
3. Single SQL generation pipeline (eliminating redundant LLM invocations).
4. Automated self-correction loop (re-prompting LLM with execution error up to 2 retries).
5. AST Guard + EXPLAIN query plan validation before execution.
"""

import os
import logging
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser
from langchain_core.messages import BaseMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from src.config import settings
from src.sql_guard import SQLSecurityGuard, SQLGuardError
from src.optimizer import optimize_query, analyze_explain_plan
from src.database import execute_query_readonly, query_engine, get_introspected_schema_dict

logger = logging.getLogger(__name__)


def get_llm():
    """Returns Google Gemini chat model instance."""
    api_key = settings.GOOGLE_API_KEY
    return ChatGoogleGenerativeAI(
        model=settings.GEMINI_MODEL,
        google_api_key=api_key or "mock_key_for_test",
        temperature=0
    )


# --- System Prompts ---

SQL_SYSTEM_PROMPT = (
    "You are an expert MySQL database administrator and query optimization engineer. "
    "Given the retrieved database schema, few-shot demonstration examples, and conversation history, "
    "generate a syntactically correct, optimized MySQL SELECT query that answers the user's question.\n\n"
    "CRITICAL OPTIMIZATION & SECURITY RULES:\n"
    "1. NEVER use SELECT *; explicitly select only the specific columns needed.\n"
    "2. Always write explicit INNER JOIN or LEFT JOIN with ON conditions matching primary/foreign keys.\n"
    "3. Push aggregations (COUNT, SUM, AVG, MIN, MAX) and filters into the SQL query.\n"
    "4. Always include a LIMIT clause (maximum 50 rows).\n"
    "5. Only query tables that exist in the provided schema.\n"
    "6. Queries must be strictly read-only. Never write INSERT, UPDATE, DELETE, DROP, ALTER, etc.\n"
    "7. Return ONLY the raw SQL query on a single line. Do not include markdown code blocks or explanations."
)

SQL_PROMPT = ChatPromptTemplate.from_messages([
    ("system", SQL_SYSTEM_PROMPT),
    ("system", "### Retrieved Table Schemas:\n{schema}\n\n### Few-Shot Demonstration Examples:\n{examples}"),
    MessagesPlaceholder(variable_name="chat_history"),
    ("human", "{question}"),
])

CORRECTION_PROMPT = ChatPromptTemplate.from_messages([
    ("system", SQL_SYSTEM_PROMPT),
    ("system", "### Retrieved Table Schemas:\n{schema}"),
    ("human", (
        "User Question: {question}\n\n"
        "Attempted SQL Query:\n{failed_sql}\n\n"
        "Database Execution Error:\n{error_message}\n\n"
        "Analyze the error, fix column/table names or syntax issues, and provide the corrected MySQL query."
    ))
])

ANSWER_PROMPT = ChatPromptTemplate.from_messages([
    ("system", (
        "You are an enterprise data assistant. Given the user question, the executed SQL query, "
        "and the query results, answer the question accurately and concisely in plain English."
    )),
    ("human", (
        "User Question: {question}\n"
        "Executed SQL Query: {query}\n"
        "SQL Query Result: {result}\n\n"
        "Answer:"
    ))
])


def build_sql_generator_chain(llm):
    """Builds chain that outputs single candidate SQL string."""
    return SQL_PROMPT | llm | StrOutputParser()


def build_sql_correction_chain(llm):
    """Builds chain for self-correcting failed queries."""
    return CORRECTION_PROMPT | llm | StrOutputParser()


def build_answer_chain(llm):
    """Builds chain that synthesizes natural language answers from query results."""
    return ANSWER_PROMPT | llm | StrOutputParser()


async def execute_rag_pipeline(
    question: str,
    chat_history: List[BaseMessage],
    rag_index,
    guard: SQLSecurityGuard,
    llm,
    max_retries: int = 2
) -> Dict[str, Any]:
    """
    Executes complete end-to-end Text-to-SQL RAG pipeline:
    1. Retrieve relevant schema chunks and few-shot examples via vector similarity.
    2. Generate candidate SQL with conversational context (MessagesPlaceholder).
    3. Validate and clamp with AST SQL Security Guard.
    4. Pass introspected schema to optimize_query and qualify/simplify expressions.
    5. Re-run AST guard on the optimized SQL.
    6. Analyze execution plan via EXPLAIN on query_engine.
    7. Execute optimized & guarded query on read-only query engine with self-correction retry loop.
    8. Synthesize natural language answer.
    """
    # 1. RAG Context Retrieval
    schema_context, examples_context, _ = rag_index.retrieve_relevant_context(
        question,
        top_k_tables=settings.TOP_K_TABLES,
        top_k_examples=settings.TOP_K_EXAMPLES
    )

    sql_chain = build_sql_generator_chain(llm)
    correction_chain = build_sql_correction_chain(llm)
    answer_chain = build_answer_chain(llm)

    # 2. Initial Candidate SQL Generation (Generated ONCE)
    candidate_sql = await sql_chain.ainvoke({
        "question": question,
        "schema": schema_context,
        "examples": examples_context,
        "chat_history": chat_history
    })

    last_error: Optional[str] = None

    # 3. Execution with Self-Correction Retry Loop
    for attempt in range(max_retries + 1):
        try:
            # 3.1: Enforce AST Guard (Regex -> AST -> Table isolation -> Limit clamp)
            sanitized_sql, tables = guard.validate_and_sanitize(candidate_sql)

            # 3.2: Pass introspected schema to optimize_query
            introspected_schema = get_introspected_schema_dict()
            optimized_sql = optimize_query(sanitized_sql, schema=introspected_schema)

            # 3.3: Re-run guard on the optimized SQL (defense-in-depth)
            final_guarded_sql, final_tables = guard.validate_and_sanitize(optimized_sql)

            # 3.4: Pass query engine to analyze_explain_plan
            explain_info = await analyze_explain_plan(
                query_engine,
                final_guarded_sql,
                max_explain_rows=settings.MAX_EXPLAIN_ROWS
            )

            # 3.5: Execute sanitized query strictly through read-only engine
            raw_results = await execute_query_readonly(final_guarded_sql)
            result_str = guard.truncate_result(str(raw_results))

            # 3.6: Generate Natural Language Answer
            final_answer = await answer_chain.ainvoke({
                "question": question,
                "query": final_guarded_sql,
                "result": result_str
            })

            return {
                "query": final_guarded_sql,
                "answer": str(final_answer),
                "rows_returned": len(raw_results),
                "retries": attempt,
                "explain": explain_info
            }

        except SQLGuardError as ge:
            # Security guard violations must fail closed immediately (never retry dangerous queries)
            logger.error(f"[SECURITY REJECTION] {ge}")
            raise

        except Exception as db_err:
            last_error = str(db_err)
            logger.warning(f"[SELF-CORRECTION] Query failed on attempt {attempt + 1}/{max_retries + 1}: {last_error}")

            if attempt < max_retries:
                # Ask LLM to correct the query using the database error
                candidate_sql = await correction_chain.ainvoke({
                    "question": question,
                    "failed_sql": candidate_sql,
                    "error_message": last_error,
                    "schema": schema_context
                })
            else:
                raise RuntimeError(f"Query execution failed after {max_retries} retries. Error: {last_error}")

    raise RuntimeError(f"Execution failed: {last_error}")