"""
SQL Security Guard Module.

Enforces multi-layer defense-in-depth security:
1. Regex pre-parser sanitization (comments, stacked queries, forbidden keyword blocklist).
2. Fail-closed sqlglot AST validation with MySQL dialect.
3. AST tree traversal rejecting DDL, DML, file system operations, and dangerous functions.
4. Table isolation enforcing allowlists and strictly blocking internal/system tables across
   joins, subqueries, CTEs, and unions.
5. Automated AST-based LIMIT and OFFSET clamping (<= MAX_ROWS, default 50).
"""

import re
import logging
from typing import Set, Optional, Tuple, List
import sqlglot
from sqlglot import exp, errors

logger = logging.getLogger(__name__)

# Hardcoded blocked internal tables and system schemas
BLOCKED_TABLES: Set[str] = {"users", "chat_logs"}
BLOCKED_SCHEMAS: Set[str] = {"information_schema", "performance_schema", "mysql", "sys"}

# Dangerous functions: DoS, execution delays, file operations, privilege enumeration
DANGEROUS_FUNCTIONS: Set[str] = {
    "SLEEP", "BENCHMARK", "LOAD_FILE", "GET_LOCK", "RELEASE_LOCK",
    "RELEASE_ALL_LOCKS", "IS_FREE_LOCK", "IS_USED_LOCK", "SYSTEM_USER",
    "SESSION_USER", "SCHEMA", "DATABASE", "VERSION"
}

# Pre-parser regex patterns
REGEX_COMMENTS = [
    re.compile(r"--[^\r\n]*"),                  # Standard SQL single-line comment
    re.compile(r"#[^\r\n]*"),                   # MySQL single-line comment
    re.compile(r"/\*.*?\*/", re.DOTALL),        # Multi-line C-style comment
]

REGEX_STACKED_QUERIES = re.compile(r";\s*\S+")

REGEX_DANGEROUS_PATTERNS = [
    re.compile(r"\bINTO\s+(OUTFILE|DUMPFILE)\b", re.IGNORECASE),
    re.compile(r"\bLOAD\s+DATA\b", re.IGNORECASE),
    re.compile(r"\bLOAD_FILE\b", re.IGNORECASE),
    re.compile(r"\b(DROP|ALTER|TRUNCATE|INSERT|UPDATE|DELETE|REPLACE|GRANT|REVOKE|LOCK|UNLOCK|EXEC|EXECUTE)\b", re.IGNORECASE),
]

# AST classes representing write/DDL/administrative mutations
FORBIDDEN_AST_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter,
    exp.TruncateTable, exp.Command, exp.Set, exp.Use, exp.Grant, exp.Merge,
    exp.Lock, exp.Into, exp.Transaction, exp.Commit, exp.Rollback, exp.Kill
)


class SQLGuardError(Exception):
    """Raised when an SQL query violates security policies, AST constraints, or table isolation."""
    def __init__(self, message: str, code: str = "SECURITY_VIOLATION"):
        super().__init__(message)
        self.message = message
        self.code = code

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class SQLSecurityGuard:
    """Enterprise AST-based SQL Security Guard with pre-parser regex defense and limit clamping."""

    def __init__(
        self,
        allowed_tables: Optional[Set[str]] = None,
        max_rows: int = 50,
        max_result_chars: int = 4000
    ):
        """
        Initialize the guard.
        
        Args:
            allowed_tables: Set of table names allowed to be queried (introspected business tables).
            max_rows: Maximum rows permitted in result set (default 50).
            max_result_chars: Maximum character length for query result string before truncation.
        """
        self.allowed_tables = {t.lower() for t in allowed_tables} if allowed_tables is not None else None
        self.max_rows = max_rows
        self.max_result_chars = max_result_chars

    def strip_markdown(self, raw_sql: str) -> str:
        """Strips markdown code fences (```sql ... ```) and leading/trailing whitespace."""
        sql = raw_sql.strip()
        if sql.startswith("```"):
            sql = re.sub(r"^```(?:sql)?\s*", "", sql, flags=re.IGNORECASE)
            sql = re.sub(r"\s*```$", "", sql)
        return sql.strip()

    def _regex_defense_layer(self, sql: str) -> str:
        """
        First layer of defense: regex validation executed prior to AST parsing.
        Denies SQL comments, stacked queries, and high-risk keywords.
        """
        # 1. Deny SQL comments (defense against comment-based injection/obfuscation)
        for pattern in REGEX_COMMENTS:
            if pattern.search(sql):
                raise SQLGuardError(
                    "SQL comments (--, #, /* */) are strictly forbidden to prevent obfuscation attacks.",
                    code="DISALLOWED_COMMENT"
                )

        # 2. Deny semicolon-stacked queries
        # Allow a single trailing semicolon, but deny any text after a semicolon
        sql_stripped = sql.rstrip(";").strip()
        if ";" in sql_stripped:
            raise SQLGuardError(
                "Multi-statement stacked queries are strictly forbidden.",
                code="STACKED_QUERY"
            )

        # 3. Deny explicit dangerous keywords via regex
        for pattern in REGEX_DANGEROUS_PATTERNS:
            match = pattern.search(sql_stripped)
            if match:
                raise SQLGuardError(
                    f"Forbidden keyword or clause detected in query: '{match.group(0)}'.",
                    code="DISALLOWED_KEYWORD"
                )

        return sql_stripped

    def validate_and_sanitize(self, raw_sql: str) -> Tuple[str, List[str]]:
        """
        Executes full defense pipeline:
        1. Pre-parser regex sanitization.
        2. Fail-closed sqlglot AST parsing.
        3. AST statement structure verification (SELECT / UNION / CTE only).
        4. Traversal forbidding DDL/DML, file I/O, and dangerous functions.
        5. Table isolation and allowlist checks.
        6. AST LIMIT / OFFSET injection and clamping.

        Returns:
            Tuple of (sanitized_sql_string, list_of_referenced_tables)
        Raises:
            SQLGuardError: If any policy is violated.
        """
        if not raw_sql or not raw_sql.strip():
            raise SQLGuardError("Empty SQL statement received.", code="EMPTY_STATEMENT")

        # Step 1: Strip markdown wrapper
        sql = self.strip_markdown(raw_sql)
        if not sql:
            raise SQLGuardError("Empty SQL statement received after markdown removal.", code="EMPTY_STATEMENT")

        # Step 2: Defense-in-depth Regex Layer
        clean_sql = self._regex_defense_layer(sql)

        # Step 3: AST Parsing (Fail closed on any parsing issue)
        try:
            parsed_stmts = sqlglot.parse(clean_sql, read="mysql")
        except errors.ParseError as e:
            raise SQLGuardError(f"SQL syntax parsing failed: {e}", code="PARSE_ERROR")
        except Exception as e:
            raise SQLGuardError(f"Unexpected error during SQL parsing: {e}", code="PARSE_ERROR")

        non_empty = [s for s in parsed_stmts if s is not None]
        if len(non_empty) == 0:
            raise SQLGuardError("No valid SQL statement found.", code="EMPTY_STATEMENT")
        if len(non_empty) > 1:
            raise SQLGuardError("Multi-statement execution is strictly blocked.", code="MULTIPLE_STATEMENTS")

        ast = non_empty[0]

        # Step 4: Validate Root Expression Type
        # A valid query must be a Select or Union (or a query containing CTEs that resolves to Select/Union)
        if not isinstance(ast, (exp.Select, exp.Union, exp.Query)):
            raise SQLGuardError(
                f"Only read-only SELECT queries are permitted. Blocked statement type: {ast.key.upper()}.",
                code="DISALLOWED_STATEMENT_TYPE"
            )

        # Step 5: Deep AST Tree Traversal for Forbidden Constructs
        for node in ast.walk():
            # Check forbidden AST classes (Insert, Update, Delete, Drop, Alter, Truncate, Into, etc.)
            if isinstance(node, FORBIDDEN_AST_NODES):
                node_name = node.key.upper() if hasattr(node, "key") else type(node).__name__.upper()
                raise SQLGuardError(
                    f"Forbidden statement or clause encountered: {node_name}.",
                    code="DISALLOWED_CONSTRUCT"
                )

            # Check INTO OUTFILE / DUMPFILE
            if isinstance(node, exp.Into):
                raise SQLGuardError("SELECT INTO / file export operations are strictly forbidden.", code="SELECT_INTO_FORBIDDEN")

            # Check dangerous and evasion functions
            if isinstance(node, (exp.CurrentVersion, exp.CurrentSchema, exp.CurrentUser)):
                raise SQLGuardError(
                    f"Execution of system metadata function '{type(node).__name__}' is blocked.",
                    code="DANGEROUS_FUNCTION"
                )
            if isinstance(node, exp.Func):
                if isinstance(node, exp.Anonymous):
                    func_name = (node.name or "").upper()
                else:
                    func_name = (getattr(node, "name", None) or node.key or "").upper()

                if func_name in DANGEROUS_FUNCTIONS:
                    raise SQLGuardError(
                        f"Execution of dangerous function '{func_name}' is blocked.",
                        code="DANGEROUS_FUNCTION"
                    )

        # Step 6: Table Isolation (joins, subqueries, CTEs, unions)
        referenced_tables = self._extract_and_validate_tables(ast)

        # Step 7: Row-Limit Clamping (inject LIMIT if missing, clamp any LIMIT/OFFSET > max_rows)
        ast = self._clamp_limits(ast, self.max_rows)

        # Generate sanitized MySQL SQL
        sanitized_sql = ast.sql(dialect="mysql")
        return sanitized_sql, referenced_tables

    def sanitize(self, raw_sql: str) -> str:
        """Convenience method returning only the sanitized SQL string."""
        sanitized_sql, _ = self.validate_and_sanitize(raw_sql)
        return sanitized_sql

    def _extract_and_validate_tables(self, ast: exp.Expression) -> List[str]:
        """
        Extracts all table references while correctly handling CTE aliases,
        and enforces both schema-level blocklists and the business table allowlist.
        """
        # Discover all CTE aliases in WITH clauses
        cte_aliases = set()
        for cte in ast.find_all(exp.CTE):
            if cte.alias:
                cte_aliases.add(cte.alias.lower())

        referenced_tables: List[str] = []

        for table_node in ast.find_all(exp.Table):
            table_name = table_node.name.lower()
            schema_name = (table_node.db or "").lower()

            # Ignore CTE aliases
            if table_name in cte_aliases:
                continue

            # Check system schemas
            if schema_name in BLOCKED_SCHEMAS:
                raise SQLGuardError(
                    f"Access to system catalog '{schema_name}' is forbidden.",
                    code="BLOCKED_SYSTEM_SCHEMA"
                )
            if table_name in BLOCKED_SCHEMAS:
                raise SQLGuardError(
                    f"Access to system catalog '{table_name}' is forbidden.",
                    code="BLOCKED_SYSTEM_SCHEMA"
                )

            # Check internal application tables
            if table_name in BLOCKED_TABLES:
                raise SQLGuardError(
                    f"Access to internal table '{table_name}' is forbidden.",
                    code="BLOCKED_INTERNAL_TABLE"
                )

            # Check allowed tables list if configured
            if self.allowed_tables is not None and table_name not in self.allowed_tables:
                raise SQLGuardError(
                    f"Table '{table_name}' is not in the list of authorized tables. "
                    f"Allowed tables: {', '.join(sorted(self.allowed_tables))}",
                    code="UNAUTHORIZED_TABLE"
                )

            if table_name not in referenced_tables:
                referenced_tables.append(table_name)

        return referenced_tables

    def _clamp_limits(self, ast: exp.Expression, max_rows: int) -> exp.Expression:
        """
        Ensures the outermost query has an enforced LIMIT <= max_rows.
        Also clamps any OFFSET or excessive LIMIT values.
        """
        # Check root limit
        limit_node = ast.args.get("limit")

        if limit_node is None:
            # Missing limit: inject LIMIT max_rows
            return ast.limit(max_rows)

        # Limit node exists: inspect row count
        try:
            limit_expr = limit_node.expression
            if limit_expr is not None and hasattr(limit_expr, "this"):
                current_limit = int(limit_expr.this)
                if current_limit > max_rows or current_limit < 0:
                    logger.info(f"Clamping requested LIMIT {current_limit} to {max_rows}")
                    limit_node.set("expression", exp.Literal.number(max_rows))
        except Exception:
            limit_node.set("expression", exp.Literal.number(max_rows))

        # Also inspect and clamp OFFSET if present
        offset_node = ast.args.get("offset")
        if offset_node is not None:
            try:
                offset_expr = offset_node.expression
                if offset_expr is not None and hasattr(offset_expr, "this"):
                    current_offset = int(offset_expr.this)
                    if current_offset > max_rows:
                        logger.info(f"Clamping requested OFFSET {current_offset} to {max_rows}")
                        offset_node.set("expression", exp.Literal.number(max_rows))
            except Exception:
                offset_node.set("expression", exp.Literal.number(max_rows))

        return ast

    def truncate_result(self, result_str: str) -> str:
        """Caps the result string to prevent blowing up LLM context tokens."""
        if len(result_str) <= self.max_result_chars:
            return result_str
        truncated = result_str[:self.max_result_chars]
        return f"{truncated}\n... [Output truncated: exceeded maximum limit of {self.max_result_chars} characters]"
