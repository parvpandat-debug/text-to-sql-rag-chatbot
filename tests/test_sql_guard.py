import pytest
from src.sql_guard import SQLSecurityGuard, SQLGuardError

@pytest.fixture
def guard():
    allowed_tables = {"orders", "customers", "products", "order_items"}
    return SQLSecurityGuard(
        allowed_tables=allowed_tables,
        max_rows=50,
        max_result_chars=200
    )

# --- 1. Valid Query & LIMIT Clamping Tests ---

def test_valid_select_injects_limit(guard):
    sql, tables = guard.validate_and_sanitize("SELECT * FROM orders")
    assert "LIMIT 50" in sql
    assert tables == ["orders"]

def test_valid_select_clamps_excessive_limit(guard):
    sql, tables = guard.validate_and_sanitize("SELECT * FROM products LIMIT 500")
    assert "LIMIT 50" in sql
    assert "LIMIT 500" not in sql
    assert tables == ["products"]

def test_valid_select_preserves_smaller_limit(guard):
    sql, tables = guard.validate_and_sanitize("SELECT * FROM customers LIMIT 10")
    assert "LIMIT 10" in sql
    assert tables == ["customers"]

def test_valid_select_clamps_excessive_offset(guard):
    sql, tables = guard.validate_and_sanitize("SELECT * FROM customers LIMIT 10 OFFSET 500")
    assert "OFFSET 50" in sql
    assert "OFFSET 500" not in sql

def test_markdown_code_block_stripping(guard):
    raw = "```sql\nSELECT id, name FROM customers;\n```"
    sql, tables = guard.validate_and_sanitize(raw)
    assert "LIMIT 50" in sql
    assert "customers" in tables

def test_join_with_allowed_tables(guard):
    raw = """
        SELECT c.name, o.id 
        FROM customers c 
        JOIN orders o ON c.id = o.customer_id
    """
    sql, tables = guard.validate_and_sanitize(raw)
    assert set(tables) == {"customers", "orders"}
    assert "LIMIT 50" in sql

def test_group_by_aggregation(guard):
    raw = "SELECT customer_id, COUNT(*) as total_orders FROM orders GROUP BY customer_id HAVING total_orders > 5"
    sql, tables = guard.validate_and_sanitize(raw)
    assert "COUNT(*)" in sql or "count(*)" in sql.lower()
    assert "HAVING" in sql
    assert tables == ["orders"]

def test_cte_query_with_allowed_tables(guard):
    raw = """
        WITH top_orders AS (
            SELECT customer_id, count(*) as cnt 
            FROM orders 
            GROUP BY customer_id
        )
        SELECT c.name, t.cnt 
        FROM top_orders t 
        JOIN customers c ON t.customer_id = c.id
    """
    sql, tables = guard.validate_and_sanitize(raw)
    # top_orders is a CTE alias, not a physical table
    assert "top_orders" not in tables
    assert set(tables) == {"orders", "customers"}

def test_union_query_with_allowed_tables(guard):
    raw = "SELECT id FROM orders UNION SELECT id FROM products"
    sql, tables = guard.validate_and_sanitize(raw)
    assert "LIMIT 50" in sql
    assert set(tables) == {"orders", "products"}

# --- 2. DDL / DML / Mutation Rejection Tests ---

@pytest.mark.parametrize("disallowed_sql", [
    "DROP TABLE orders",
    "DELETE FROM orders WHERE id = 1",
    "UPDATE orders SET status = 'cancelled'",
    "INSERT INTO orders (id) VALUES (100)",
    "ALTER TABLE orders ADD COLUMN test INT",
    "TRUNCATE TABLE orders",
    "CREATE TABLE evil (id INT)",
    "GRANT ALL ON *.* TO 'attacker'@'%'",
    "REVOKE ALL ON *.* FROM 'user'@'%'",
    "LOCK TABLES orders WRITE",
    "SET GLOBAL general_log = 1",
    "REPLACE INTO orders (id) VALUES (10)",
])
def test_blocks_mutation_and_ddl(guard, disallowed_sql):
    with pytest.raises(SQLGuardError) as exc:
        guard.validate_and_sanitize(disallowed_sql)
    assert any(code in exc.value.code for code in ["DISALLOWED_KEYWORD", "DISALLOWED_STATEMENT_TYPE", "DISALLOWED_CONSTRUCT"])

def test_blocks_mixed_case_mutation(guard):
    with pytest.raises(SQLGuardError) as exc:
        guard.validate_and_sanitize("dRoP tAbLe orders")
    assert "DISALLOWED_KEYWORD" in exc.value.code or "DISALLOWED_CONSTRUCT" in exc.value.code

# --- 3. Defense-in-Depth: Comments & Stacked Queries ---

@pytest.mark.parametrize("commented_sql", [
    "SELECT * FROM orders -- comment to hide attack",
    "SELECT * FROM orders # inline mysql comment",
    "SELECT * FROM orders /* multi-line comment */",
    "/* comment */ SELECT * FROM orders",
])
def test_blocks_comment_obfuscated_attacks(guard, commented_sql):
    with pytest.raises(SQLGuardError) as exc:
        guard.validate_and_sanitize(commented_sql)
    assert exc.value.code == "DISALLOWED_COMMENT"

@pytest.mark.parametrize("stacked_sql", [
    "SELECT * FROM orders; DROP TABLE customers;",
    "SELECT id FROM products; SELECT * FROM users;",
    "SELECT 1; UPDATE orders SET status = 'shipped';",
])
def test_blocks_stacked_queries(guard, stacked_sql):
    with pytest.raises(SQLGuardError) as exc:
        guard.validate_and_sanitize(stacked_sql)
    assert exc.value.code in ["STACKED_QUERY", "MULTIPLE_STATEMENTS", "DISALLOWED_KEYWORD"]

# --- 4. File I/O & Dangerous Functions ---

@pytest.mark.parametrize("file_sql", [
    "SELECT * FROM orders INTO OUTFILE '/tmp/dump.txt'",
    "SELECT * FROM orders INTO DUMPFILE '/tmp/dump.txt'",
    "SELECT LOAD_FILE('/etc/passwd')",
])
def test_blocks_file_io_operations(guard, file_sql):
    with pytest.raises(SQLGuardError) as exc:
        guard.validate_and_sanitize(file_sql)
    assert any(c in exc.value.code for c in ["DISALLOWED_KEYWORD", "SELECT_INTO_FORBIDDEN", "DANGEROUS_FUNCTION"])

@pytest.mark.parametrize("func_sql", [
    "SELECT SLEEP(5) FROM orders",
    "SELECT BENCHMARK(1000000, MD5('test')) FROM orders",
    "SELECT GET_LOCK('test_lock', 10)",
    "SELECT RELEASE_LOCK('test_lock')",
    "SELECT SYSTEM_USER()",
    "SELECT VERSION()",
])
def test_blocks_dangerous_functions(guard, func_sql):
    with pytest.raises(SQLGuardError) as exc:
        guard.validate_and_sanitize(func_sql)
    assert exc.value.code == "DANGEROUS_FUNCTION"

# --- 5. Table Isolation (Direct, Subquery, JOIN, CTE, UNION) ---

@pytest.mark.parametrize("catalog_sql", [
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM `information_schema`.columns",
    "SELECT * FROM mysql.user",
    "SELECT * FROM performance_schema.threads",
    "SELECT * FROM sys.schema_table_statistics",
])
def test_blocks_system_catalogs(guard, catalog_sql):
    with pytest.raises(SQLGuardError) as exc:
        guard.validate_and_sanitize(catalog_sql)
    assert exc.value.code == "BLOCKED_SYSTEM_SCHEMA"

@pytest.mark.parametrize("internal_sql", [
    "SELECT * FROM users",
    "SELECT username, hashed_password FROM users",
    "SELECT * FROM chat_logs",
    "SELECT o.id, u.username FROM orders o JOIN users u ON o.customer_id = u.id",
    "SELECT * FROM orders WHERE customer_id IN (SELECT id FROM users)",
    "WITH secret AS (SELECT * FROM users) SELECT * FROM secret",
    "SELECT id FROM orders UNION SELECT id FROM users",
])
def test_blocks_internal_app_tables_in_all_constructs(guard, internal_sql):
    with pytest.raises(SQLGuardError) as exc:
        guard.validate_and_sanitize(internal_sql)
    assert exc.value.code in ["BLOCKED_INTERNAL_TABLE", "UNAUTHORIZED_TABLE"]

def test_blocks_unauthorized_tables(guard):
    with pytest.raises(SQLGuardError) as exc:
        guard.validate_and_sanitize("SELECT * FROM salaries")
    assert exc.value.code == "UNAUTHORIZED_TABLE"
    assert "salaries" in str(exc.value)

def test_empty_sql_rejection(guard):
    with pytest.raises(SQLGuardError) as exc:
        guard.validate_and_sanitize("   ")
    assert exc.value.code == "EMPTY_STATEMENT"

def test_truncate_result(guard):
    short_text = "Hello World"
    assert guard.truncate_result(short_text) == short_text
    
    long_text = "x" * 300
    truncated = guard.truncate_result(long_text)
    assert len(truncated) < 300
    assert "[Output truncated:" in truncated
