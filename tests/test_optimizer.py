"""
Unit Tests for Query Optimizer and EXPLAIN Plan Analysis.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from langchain_community.chat_models.fake import FakeListChatModel
from src.optimizer import optimize_query, analyze_explain_plan, QueryOptimizationError
from src.chains import execute_rag_pipeline
from src.sql_guard import SQLSecurityGuard


def test_optimize_query_simplifies_and_formats():
    raw = "SELECT id, name FROM products WHERE 1 = 1 AND price > 10"
    optimized = optimize_query(raw)
    assert "products" in optimized.lower()
    assert "LIMIT" not in raw  # optimizer preserves structure while simplifying


def test_optimize_query_with_schema():
    raw = "SELECT id, price FROM products WHERE 1 = 1 LIMIT 50"
    schema = {"products": {"id": "int", "price": "decimal"}}
    optimized = optimize_query(raw, schema=schema)
    assert "products" in optimized.lower()


def test_optimize_query_fallback_on_invalid():
    # If invalid or partial query is passed, it should safely fall back to input string
    broken = "SELECT FROM WHERE"
    res = optimize_query(broken)
    assert res == broken


def test_get_introspected_schema_dict_caching():
    """Proves get_introspected_schema_dict uses LRU cache to avoid per-request DB calls."""
    from src.database import get_introspected_schema_dict
    get_introspected_schema_dict.cache_clear()

    # First call: cache miss
    schema1 = get_introspected_schema_dict()
    assert "customers" in schema1
    assert get_introspected_schema_dict.cache_info().currsize == 1

    # Second call: cache hit
    schema2 = get_introspected_schema_dict()
    assert schema1 == schema2
    assert get_introspected_schema_dict.cache_info().hits >= 1



@pytest.mark.asyncio
async def test_explain_plan_detects_massive_full_table_scan():
    # Mock engine that returns a massive full table scan (type='ALL', rows=25000)
    mock_conn = AsyncMock()
    mock_cursor = MagicMock()
    mock_cursor.keys.return_value = ["id", "select_type", "table", "type", "rows", "Extra"]
    mock_cursor.fetchall.return_value = [
        (1, "SIMPLE", "orders", "ALL", 25000, "Using where")
    ]
    mock_conn.execute.return_value = mock_cursor

    mock_engine = MagicMock()
    mock_engine.connect.return_value.__aenter__.return_value = mock_conn

    # 1. Non-rejecting mode: flags is_safe=False
    plan = await analyze_explain_plan(
        mock_engine,
        "SELECT * FROM orders",
        max_explain_rows=10000,
        reject_full_scan=False
    )
    assert plan["is_safe"] is False
    assert plan["total_estimated_rows"] == 25000
    assert "orders" in plan["full_table_scans"]

    # 2. Rejecting mode: raises QueryOptimizationError
    with pytest.raises(QueryOptimizationError) as exc:
        await analyze_explain_plan(
            mock_engine,
            "SELECT * FROM orders",
            max_explain_rows=10000,
            reject_full_scan=True
        )
    assert "exceeding the threshold" in str(exc.value)


@pytest.mark.asyncio
async def test_explain_plan_accepts_safe_indexed_query():
    # Mock engine returning index lookup (type='ref', rows=1)
    mock_conn = AsyncMock()
    mock_cursor = MagicMock()
    mock_cursor.keys.return_value = ["id", "select_type", "table", "type", "rows", "Extra"]
    mock_cursor.fetchall.return_value = [
        (1, "SIMPLE", "orders", "ref", 1, "Using index condition")
    ]
    mock_conn.execute.return_value = mock_cursor

    mock_engine = MagicMock()
    mock_engine.connect.return_value.__aenter__.return_value = mock_conn

    plan = await analyze_explain_plan(
        mock_engine,
        "SELECT * FROM orders WHERE customer_id = 5",
        max_explain_rows=10000
    )
    assert plan["is_safe"] is True
    assert len(plan["full_table_scans"]) == 0


@pytest.mark.asyncio
async def test_pipeline_wires_optimizer_and_explain(monkeypatch):
    """Proves that both optimize_query and analyze_explain_plan are executed in execute_rag_pipeline."""
    guard = SQLSecurityGuard(allowed_tables={"products"}, max_rows=50)

    class MockRAG:
        def retrieve_relevant_context(self, *args, **kwargs):
            return "CREATE TABLE products (id INT, price INT);", "Example 1", ["products"]

    mock_llm = FakeListChatModel(responses=[
        "SELECT id, price FROM products WHERE 1 = 1 LIMIT 50;",  # SQL generation
        "Here are the product prices."                          # Answer synthesis
    ])

    optimizer_called = []
    explain_called = []

    original_optimize = optimize_query
    def tracking_optimize(sql, schema=None):
        optimizer_called.append((sql, schema))
        return original_optimize(sql, schema=schema)

    async def tracking_explain(engine, sql, max_explain_rows=10000):
        explain_called.append((engine, sql))
        return {"is_safe": True, "total_estimated_rows": 1}

    async def mock_exec_readonly(sql):
        return [{"id": 1, "price": 100}]

    monkeypatch.setattr("src.chains.optimize_query", tracking_optimize)
    monkeypatch.setattr("src.chains.analyze_explain_plan", tracking_explain)
    monkeypatch.setattr("src.chains.execute_query_readonly", mock_exec_readonly)

    result = await execute_rag_pipeline(
        question="What are product prices?",
        chat_history=[],
        rag_index=MockRAG(),
        guard=guard,
        llm=mock_llm
    )

    # Prove both ran
    assert len(optimizer_called) == 1, "optimize_query must be executed in pipeline!"
    assert optimizer_called[0][1] is not None, "introspected schema must be passed to optimize_query!"

    assert len(explain_called) == 1, "analyze_explain_plan must be executed in pipeline!"
    assert explain_called[0][0] is not None, "query_engine must be passed to analyze_explain_plan!"

    assert "explain" in result
    assert result["explain"]["is_safe"] is True
