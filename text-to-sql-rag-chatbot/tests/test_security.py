import pytest
from src.security import SQLSecurityGuard, SQLSecurityError

@pytest.fixture
def guard():
    allowed_tables = {"orders", "customers", "products", "order_items"}
    return SQLSecurityGuard(
        allowed_tables=allowed_tables,
        max_limit=50,
        max_result_chars=200
    )

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
    assert "top_orders" not in tables
    assert set(tables) == {"orders", "customers"}

def test_union_query_with_allowed_tables(guard):
    raw = "SELECT id FROM orders UNION SELECT id FROM products"
    sql, tables = guard.validate_and_sanitize(raw)
    assert "LIMIT 50" in sql
    assert set(tables) == {"orders", "products"}

@pytest.mark.parametrize("disallowed_sql", [
    "DROP TABLE orders",
    "DELETE FROM orders WHERE id = 1",
    "UPDATE orders SET status = 'cancelled'",
    "INSERT INTO orders (id) VALUES (100)",
    "ALTER TABLE orders ADD COLUMN test INT",
    "TRUNCATE TABLE orders",
    "CREATE TABLE evil (id INT)",
    "GRANT ALL ON *.* TO 'attacker'@'%'",
    "SET GLOBAL general_log = 1",
])
def test_blocks_mutation_and_ddl(guard, disallowed_sql):
    with pytest.raises(SQLSecurityError) as exc:
        guard.validate_and_sanitize(disallowed_sql)
    assert "Only read-only SELECT queries are permitted" in str(exc.value)

def test_blocks_multi_statements(guard):
    with pytest.raises(SQLSecurityError) as exc:
        guard.validate_and_sanitize("SELECT * FROM orders; DROP TABLE customers;")
    assert "Multi-statement execution is strictly blocked" in str(exc.value)

@pytest.mark.parametrize("system_query", [
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM `information_schema`.columns",
    "SELECT * FROM mysql.user",
    "SELECT * FROM performance_schema.threads",
    "SELECT * FROM sys.schema_table_statistics",
])
def test_blocks_system_catalogs(guard, system_query):
    with pytest.raises(SQLSecurityError) as exc:
        guard.validate_and_sanitize(system_query)
    assert "forbidden" in str(exc.value).lower()

@pytest.mark.parametrize("internal_table_query", [
    "SELECT * FROM users",
    "SELECT username, hashed_password FROM users",
    "SELECT * FROM chat_logs",
    "SELECT o.id, u.hashed_password FROM orders o JOIN users u ON o.customer_id = u.id",
    "SELECT * FROM orders WHERE id IN (SELECT id FROM users)",
])
def test_blocks_internal_app_tables(guard, internal_table_query):
    with pytest.raises(SQLSecurityError) as exc:
        guard.validate_and_sanitize(internal_table_query)
    assert "forbidden" in str(exc.value).lower()

@pytest.mark.parametrize("dangerous_func_query", [
    "SELECT SLEEP(10) FROM orders",
    "SELECT BENCHMARK(1000000, MD5(1)) FROM orders",
    "SELECT LOAD_FILE('/etc/passwd') FROM orders",
])
def test_blocks_dangerous_functions(guard, dangerous_func_query):
    with pytest.raises(SQLSecurityError) as exc:
        guard.validate_and_sanitize(dangerous_func_query)
    assert "unauthorized SQL function" in str(exc.value)

def test_blocks_unauthorized_tables(guard):
    with pytest.raises(SQLSecurityError) as exc:
        guard.validate_and_sanitize("SELECT * FROM employee_salaries")
    assert "not in the list of authorized tables" in str(exc.value)

def test_blocks_into_outfile(guard):
    with pytest.raises(SQLSecurityError) as exc:
        guard.validate_and_sanitize("SELECT * INTO OUTFILE '/tmp/dump.csv' FROM orders")
    assert "Disallowed file/dump operation" in str(exc.value) or "forbidden" in str(exc.value)

def test_result_truncation(guard):
    short_str = "Row 1, Row 2"
    assert guard.truncate_result(short_str) == short_str

    long_str = "x" * 300
    truncated = guard.truncate_result(long_str)
    assert len(truncated) > 200
    assert "[Output truncated: exceeded maximum limit of 200 characters]" in truncated
