import re
import logging
from typing import Set, Optional, Tuple, List
import sqlglot
from sqlglot import exp, errors

logger = logging.getLogger(__name__)

BLOCKED_TABLES: Set[str] = {"users", "chat_logs"}
BLOCKED_SCHEMAS: Set[str] = {"information_schema", "performance_schema", "mysql", "sys"}
DANGEROUS_FUNCTIONS: Set[str] = {
    "SLEEP", "BENCHMARK", "LOAD_FILE", "GET_LOCK", "RELEASE_LOCK",
    "RELEASE_ALL_LOCKS", "IS_FREE_LOCK", "IS_USED_LOCK", "SYSTEM_USER",
    "SESSION_USER", "SCHEMA", "DATABASE", "VERSION"
}
DISALLOWED_PATTERNS = [
    re.compile(r"\bINTO\s+(OUTFILE|DUMPFILE)\b", re.IGNORECASE),
    re.compile(r"\bLOAD\s+DATA\b", re.IGNORECASE),
]

class SQLSecurityError(Exception):
    """Raised when an SQL query violates security policies."""
    pass

class SQLSecurityGuard:
    """Enforces strict read-only execution, table allowlisting, and result size clamping via AST."""

    def __init__(
        self,
        allowed_tables: Optional[Set[str]] = None,
        max_limit: int = 50,
        max_result_chars: int = 4000
    ):
        self.allowed_tables = {t.lower() for t in allowed_tables} if allowed_tables else None
        self.max_limit = max_limit
        self.max_result_chars = max_result_chars

    def clean_markdown_and_whitespace(self, raw_sql: str) -> str:
        sql = raw_sql.strip()
        if sql.startswith("```"):
            sql = re.sub(r"^```(?:sql)?\s*", "", sql, flags=re.IGNORECASE)
            sql = re.sub(r"\s*```$", "", sql)
        return sql.strip()

    def validate_and_sanitize(self, raw_sql: str) -> Tuple[str, List[str]]:
        """Parses the SQL using sqlglot, enforces security gates, and injects/clamps LIMIT.

        Returns:
            Tuple of (sanitized_sql_string, list_of_referenced_tables)
        Raises:
            SQLSecurityError: If any policy is violated.
        """
        sql = self.clean_markdown_and_whitespace(raw_sql)
        if not sql:
            raise SQLSecurityError("Empty SQL statement received.")

        # Defense-in-depth: regex check for dangerous clauses that might evade parser
        for pattern in DISALLOWED_PATTERNS:
            if pattern.search(sql):
                raise SQLSecurityError("Disallowed file/dump operation detected in query.")

        # 1. Parse statement(s)
        try:
            parsed_stmts = sqlglot.parse(sql, read="mysql")
        except errors.ParseError as e:
            raise SQLSecurityError(f"SQL syntax parsing failed: {e}")

        # Filter out empty statements (like stray semicolons)
        non_empty_stmts = [s for s in parsed_stmts if s is not None]
        if len(non_empty_stmts) == 0:
            raise SQLSecurityError("No valid SQL statements found.")
        if len(non_empty_stmts) > 1:
            raise SQLSecurityError("Multi-statement execution is strictly blocked for security.")

        ast = non_empty_stmts[0]

        # 2. Hard Gate: Pure Read-Only AST Validation
        if not isinstance(ast, exp.Query):
            raise SQLSecurityError(
                f"Only read-only SELECT queries are permitted. Blocked statement type: {ast.key.upper()}."
            )

        # 3. Disallow INTO clause (e.g. SELECT ... INTO OUTFILE / INTO table)
        if ast.find(exp.Into) is not None:
            raise SQLSecurityError("SELECT INTO statements are strictly forbidden.")

        # 4. Check for dangerous/evasion functions
        called_functions = set()
        for func in ast.find_all(exp.Func):
            called_functions.add(func.key.upper())
        for anon in ast.find_all(exp.Anonymous):
            called_functions.add(anon.name.upper())

        blacklisted_funcs = called_functions.intersection(DANGEROUS_FUNCTIONS)
        if blacklisted_funcs:
            raise SQLSecurityError(
                f"Blocked execution of unauthorized SQL function(s): {', '.join(blacklisted_funcs)}"
            )

        # 5. Extract tables and validate against allowlist & blocklist
        ctes = {c.alias.lower() for c in ast.find_all(exp.CTE) if c.alias}
        referenced_tables: List[str] = []

        for table_node in ast.find_all(exp.Table):
            table_name = table_node.name.lower()
            schema_name = (table_node.db or "").lower()

            # Ignore CTE aliases
            if table_name in ctes:
                continue

            # Check system schemas
            if schema_name in BLOCKED_SCHEMAS:
                raise SQLSecurityError(f"Access to system catalog '{schema_name}' is forbidden.")

            # Check internal app tables
            if table_name in BLOCKED_TABLES:
                raise SQLSecurityError(f"Access to internal table '{table_name}' is forbidden.")

            # Check allowed tables list if configured
            if self.allowed_tables is not None and table_name not in self.allowed_tables:
                raise SQLSecurityError(
                    f"Table '{table_name}' is not in the list of authorized tables. "
                    f"Allowed tables: {', '.join(sorted(self.allowed_tables))}"
                )

            referenced_tables.append(table_name)

        # 6. Result size limits: Enforce or Clamp LIMIT clause
        ast = self._enforce_limit(ast, self.max_limit)

        sanitized_sql = ast.sql(dialect="mysql")
        return sanitized_sql, referenced_tables

    def _enforce_limit(self, ast: exp.Expression, max_limit: int) -> exp.Expression:
        """Injects or clamps LIMIT on the outer query."""
        limit_node = ast.find(exp.Limit)
        if not limit_node:
            return ast.limit(max_limit)

        try:
            current_limit = int(limit_node.expression.this)
            if current_limit > max_limit:
                logger.info(f"Clamping requested LIMIT {current_limit} down to {max_limit}")
                return ast.limit(max_limit)
        except Exception:
            return ast.limit(max_limit)

        return ast

    def truncate_result(self, result_str: str) -> str:
        """Caps the result string to prevent blowing up LLM context tokens."""
        if len(result_str) <= self.max_result_chars:
            return result_str
        truncated = result_str[:self.max_result_chars]
        return f"{truncated}\n... [Output truncated: exceeded maximum limit of {self.max_result_chars} characters]"
