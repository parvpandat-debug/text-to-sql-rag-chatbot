"""
SQL Query Optimizer and Execution Plan (EXPLAIN) Analyzer.

Enforces:
1. AST-level query simplification and qualification via sqlglot optimizer.
2. EXPLAIN query execution plan inspection before database execution.
3. Detection and rejection/flagging of dangerous full table scans exceeding row thresholds.
"""

import logging
from typing import Dict, Any, List, Optional
import sqlglot
from sqlglot import errors
from sqlglot.optimizer import optimize
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


class QueryOptimizationError(Exception):
    """Raised when an EXPLAIN plan violates execution bounds (e.g. massive full table scan)."""
    pass


def optimize_query(sanitized_sql: str, schema: Optional[Dict[str, Any]] = None) -> str:
    """
    Optimizes the sanitized SQL using sqlglot's optimizer rule pipeline.
    Simplifies boolean expressions, eliminates redundant projections, qualifies columns.
    
    Falls back safely to the original sanitized SQL if optimizer encountered an edge-case.
    """
    try:
        expression = sqlglot.parse_one(sanitized_sql, read="mysql")
        optimized = optimize(expression, schema=schema, dialect="mysql")
        return optimized.sql(dialect="mysql")
    except Exception as e:
        logger.debug(f"Optimizer pass skipped due to: {e}. Preserving sanitized SQL.")
        return sanitized_sql


async def analyze_explain_plan(
    engine: AsyncEngine,
    sanitized_sql: str,
    max_explain_rows: int = 10000,
    reject_full_scan: bool = False
) -> Dict[str, Any]:
    """
    Runs EXPLAIN on the sanitized query using the read-only query engine.
    Analyzes join types (detecting 'ALL' full table scans) and total estimated rows.
    
    Args:
        engine: The async read-only query engine.
        sanitized_sql: The sanitized and clamped SQL query.
        max_explain_rows: Maximum estimated rows permitted for full table scans.
        reject_full_scan: If True, raises QueryOptimizationError on massive full scans.
        
    Returns:
        Dict containing total estimated rows, full table scan tables, and safety status.
    """
    explain_sql = f"EXPLAIN {sanitized_sql.rstrip(';')}"
    
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text(explain_sql))
            keys = [k.lower() for k in result.keys()]
            rows = result.fetchall()
    except Exception as e:
        logger.warning(f"EXPLAIN analysis could not be executed: {e}")
        return {"is_safe": True, "error": str(e), "total_estimated_rows": 0}

    total_estimated_rows = 0
    full_table_scans: List[str] = []
    explain_data = []

    for r in rows:
        row_dict = dict(zip(keys, r))
        explain_data.append(row_dict)

        # In MySQL EXPLAIN: 'type' column indicates access method ('ALL' = full table scan)
        access_type = str(row_dict.get("type", "")).upper()
        tbl = str(row_dict.get("table", "unknown"))
        est_rows = row_dict.get("rows", 0) or 0

        try:
            est_rows = int(est_rows)
        except (ValueError, TypeError):
            est_rows = 0

        total_estimated_rows += est_rows

        if access_type == "ALL" and tbl != "unknown":
            full_table_scans.append(tbl)

    is_safe = True
    reason = None

    if total_estimated_rows > max_explain_rows and full_table_scans:
        is_safe = False
        reason = (
            f"Query triggers full table scan on {full_table_scans} with "
            f"{total_estimated_rows} estimated rows, exceeding the threshold of {max_explain_rows}."
        )
        logger.warning(f"[EXPLAIN ALERT] {reason}")
        if reject_full_scan:
            raise QueryOptimizationError(reason)

    return {
        "is_safe": is_safe,
        "reason": reason,
        "total_estimated_rows": total_estimated_rows,
        "full_table_scans": full_table_scans,
        "explain_rows": explain_data
    }
