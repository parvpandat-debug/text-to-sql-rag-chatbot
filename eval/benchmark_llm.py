"""
End-to-End LLM Benchmark Suite for Text-to-SQL RAG Chatbot.

Evaluates multi-turn conversational Text-to-SQL with Google Gemini across
chained follow-ups and enterprise business analytics questions.

Measures:
- End-to-End Latency (including Gemini LLM generation, RAG retrieval, AST guard,
  query optimization, EXPLAIN plan analysis, DB execution, and answer synthesis)
- SQL Execution Accuracy (Equivalence matching vs. Gold query result set)
- SQL Validity Rate (Queries passing MySQL/AST parser without syntax or execution error)
- Conversational Context Retention (Successful resolution of chained follow-up questions)

Usage:
  Live Gemini Run : python eval/benchmark_llm.py
  Offline Mock Run: python eval/benchmark_llm.py --mock
"""

import os
import sys
import time
import asyncio
import sqlite3
import argparse
from pathlib import Path
from typing import List, Dict, Any, Tuple

# Ensure project root in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from src.config import settings
from src.sql_guard import SQLSecurityGuard, SQLGuardError
from src.optimizer import optimize_query
from src.chains import execute_rag_pipeline, get_llm
from src.rag import get_rag_index

# Conversational Chains with Chained Follow-Ups
BENCHMARK_CONVERSATIONS = [
    {
        "chain_name": "Conversation 1: Customer Demographics & Onboarding",
        "turns": [
            {
                "id": "C1-T1",
                "question": "Show all customers living in New York.",
                "gold_sql": "SELECT id, name, email, city FROM customers WHERE city = 'New York' LIMIT 50;",
                "is_followup": False
            },
            {
                "id": "C1-T2",
                "question": "Which of those signed up after March 2025?",
                "gold_sql": "SELECT id, name, email, city, signup_date FROM customers WHERE city = 'New York' AND signup_date > '2025-03-01' LIMIT 50;",
                "is_followup": True
            },
            {
                "id": "C1-T3",
                "question": "How many total customers is that?",
                "gold_sql": "SELECT COUNT(*) AS total_customers FROM customers WHERE city = 'New York' AND signup_date > '2025-03-01' LIMIT 50;",
                "is_followup": True
            }
        ]
    },
    {
        "chain_name": "Conversation 2: Customer Order History & Spend",
        "turns": [
            {
                "id": "C2-T1",
                "question": "Show orders placed by customer 1 sorted by date descending.",
                "gold_sql": "SELECT id, total_amount, status, order_date FROM orders WHERE customer_id = 1 ORDER BY order_date DESC LIMIT 50;",
                "is_followup": False
            },
            {
                "id": "C2-T2",
                "question": "Which of those orders were completed?",
                "gold_sql": "SELECT id, total_amount, status, order_date FROM orders WHERE customer_id = 1 AND status = 'completed' ORDER BY order_date DESC LIMIT 50;",
                "is_followup": True
            },
            {
                "id": "C2-T3",
                "question": "How much did customer 1 spend in total on those completed orders?",
                "gold_sql": "SELECT SUM(total_amount) AS customer_revenue FROM orders WHERE customer_id = 1 AND status = 'completed' LIMIT 50;",
                "is_followup": True
            }
        ]
    },
    {
        "chain_name": "Conversation 3: Catalog Exploration & Inventory",
        "turns": [
            {
                "id": "C3-T1",
                "question": "List all products in the Electronics category.",
                "gold_sql": "SELECT id, name, price, stock FROM products WHERE category = 'Electronics' LIMIT 50;",
                "is_followup": False
            },
            {
                "id": "C3-T2",
                "question": "Find products among those priced under 100 dollars.",
                "gold_sql": "SELECT id, name, price, stock FROM products WHERE category = 'Electronics' AND price < 100.00 LIMIT 50;",
                "is_followup": True
            }
        ]
    },
    {
        "chain_name": "Conversation 4: Revenue & Order Aggregations",
        "turns": [
            {
                "id": "C4-T1",
                "question": "Calculate the total revenue generated from all completed orders.",
                "gold_sql": "SELECT SUM(total_amount) AS total_revenue FROM orders WHERE status = 'completed' LIMIT 50;",
                "is_followup": False
            },
            {
                "id": "C4-T2",
                "question": "Count how many orders exist in each status.",
                "gold_sql": "SELECT status, COUNT(*) AS order_count FROM orders GROUP BY status LIMIT 50;",
                "is_followup": False
            }
        ]
    }
]


def setup_benchmark_database() -> sqlite3.Connection:
    """Sets up an in-memory SQLite database populated with benchmark schema and seed rows."""
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()

    cur.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, email TEXT, city TEXT, signup_date TEXT);")
    cur.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, category TEXT, price REAL, stock INTEGER);")
    cur.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER, total_amount REAL, status TEXT, order_date TEXT);")
    cur.execute("CREATE TABLE order_items (id INTEGER PRIMARY KEY, order_id INTEGER, product_id INTEGER, quantity INTEGER, unit_price REAL);")

    # Seed customers
    for i in range(1, 21):
        city = ["New York", "San Francisco", "Chicago", "Austin", "Seattle"][i % 5]
        cur.execute("INSERT INTO customers VALUES (?, ?, ?, ?, ?)",
                    (i, f"Customer {i}", f"cust{i}@example.com", city, f"2025-0{1 + (i % 8)}-15"))

    # Seed products
    for i in range(1, 21):
        cat = ["Electronics", "Furniture", "Apparel", "Books"][i % 4]
        price = round(20.0 + (i * 35.5), 2)
        cur.execute("INSERT INTO products VALUES (?, ?, ?, ?, ?)",
                    (i, f"Product {i}", cat, price, 10 + (i * 10)))

    # Seed 210 orders
    for i in range(1, 211):
        cust_id = ((i - 1) % 20) + 1
        status = ["completed", "shipped", "pending", "cancelled"][i % 4]
        amount = round(40.0 + ((i * 13.7) % 500), 2)
        cur.execute("INSERT INTO orders VALUES (?, ?, ?, ?, ?)",
                    (i, cust_id, amount, status, "2025-05-10"))

    # Seed order items
    for i in range(1, 211):
        prod_id = ((i - 1) % 20) + 1
        cur.execute("INSERT INTO order_items VALUES (?, ?, ?, ?, ?)",
                    (i, i, prod_id, 1, 50.0))

    conn.commit()
    return conn


def normalize_result_set(rows: List[Tuple]) -> List[Tuple]:
    """Normalizes result rows for order-agnostic and floating-point comparison."""
    norm = []
    for row in rows:
        norm_row = []
        for val in row:
            if isinstance(val, float):
                norm_row.append(round(val, 2))
            elif isinstance(val, (int, str)):
                norm_row.append(val)
            elif val is None:
                norm_row.append(None)
            else:
                norm_row.append(str(val))
        norm.append(tuple(norm_row))
    return sorted(norm, key=lambda x: str(x))


async def run_llm_benchmark(use_mock: bool = False) -> Dict[str, Any]:
    """Runs the end-to-end LLM benchmark suite with Gemini and chained follow-ups."""
    print("=" * 75)
    print("STARTING END-TO-END GEMINI LLM TEXT-TO-SQL BENCHMARK")
    print("=" * 75)

    api_key = settings.GOOGLE_API_KEY
    if not api_key and not use_mock:
        print("ERROR: GOOGLE_API_KEY is not configured in .env or environment.")
        print("To run the live Gemini benchmark:")
        print("  1. Configure GOOGLE_API_KEY in your environment or .env file.")
        print("  2. Run: python eval/benchmark_llm.py")
        print("\nTo test the benchmark harness offline with mock LLM:")
        print("  python eval/benchmark_llm.py --mock")
        print("=" * 75)
        sys.exit(1)

    # Initialize components
    db_conn = setup_benchmark_database()
    guard = SQLSecurityGuard(
        allowed_tables={"customers", "products", "orders", "order_items"},
        max_rows=50
    )
    rag_index = get_rag_index()

    if use_mock:
        from langchain_community.chat_models.fake import FakeListChatModel
        print("[MODE] Running in Mock LLM simulation mode (offline verification).")
        # Build responses matching gold queries and answers
        mock_responses = []
        for conv in BENCHMARK_CONVERSATIONS:
            for turn in conv["turns"]:
                mock_responses.append(turn["gold_sql"])
                mock_responses.append("Here is the requested data based on your query.")
        llm = FakeListChatModel(responses=mock_responses)
    else:
        print("[MODE] Running with live Google Gemini model:", settings.GEMINI_MODEL)
        llm = get_llm()

    # Create adapter to execute queries against benchmark SQLite database
    import src.chains as chains_module

    async def benchmark_exec_readonly(sql: str):
        cur = db_conn.cursor()
        cur.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    async def benchmark_explain(engine, sql: str, max_explain_rows=10000):
        # Explain adapter for SQLite
        cur = db_conn.cursor()
        try:
            cur.execute(f"EXPLAIN QUERY PLAN {sql}")
            return {"is_safe": True, "total_estimated_rows": 10, "full_table_scans": []}
        except Exception:
            return {"is_safe": True, "total_estimated_rows": 0, "full_table_scans": []}

    orig_exec = chains_module.execute_query_readonly
    orig_explain = chains_module.analyze_explain_plan
    chains_module.execute_query_readonly = benchmark_exec_readonly
    chains_module.analyze_explain_plan = benchmark_explain

    total_turns = sum(len(c["turns"]) for c in BENCHMARK_CONVERSATIONS)
    turn_counter = 0
    sql_valid_count = 0
    accuracy_match_count = 0
    latencies_ms: List[float] = []
    details = []

    try:
        for conv in BENCHMARK_CONVERSATIONS:
            print(f"\n--- {conv['chain_name']} ---")
            chat_history: List[BaseMessage] = []

            for turn in conv["turns"]:
                turn_counter += 1
                t_id = turn["id"]
                q_text = turn["question"]
                gold_sql = turn["gold_sql"]
                is_followup = turn["is_followup"]

                t0 = time.perf_counter()
                is_valid = False
                is_match = False
                error_msg = None
                executed_sql = ""
                answer_text = ""

                try:
                    result = await execute_rag_pipeline(
                        question=q_text,
                        chat_history=chat_history,
                        rag_index=rag_index,
                        guard=guard,
                        llm=llm
                    )
                    executed_sql = result["query"]
                    answer_text = result["answer"]
                    is_valid = True
                    sql_valid_count += 1

                    # Result Set Comparison with Gold Query
                    cur = db_conn.cursor()
                    cur.execute(gold_sql)
                    gold_rows = cur.fetchall()

                    cur.execute(executed_sql)
                    cand_rows = cur.fetchall()

                    norm_gold = normalize_result_set(gold_rows)
                    norm_cand = normalize_result_set(cand_rows)

                    if norm_gold == norm_cand:
                        is_match = True
                        accuracy_match_count += 1
                    else:
                        error_msg = f"Mismatch: expected {len(norm_gold)} rows, got {len(norm_cand)} rows."

                    # Maintain Conversational History
                    chat_history.append(HumanMessage(content=q_text))
                    chat_history.append(AIMessage(content=answer_text))

                except SQLGuardError as ge:
                    error_msg = f"Guard rejection: {ge.message}"
                except Exception as e:
                    error_msg = f"Execution failure: {e}"

                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                latencies_ms.append(elapsed_ms)

                status_flag = "PASS" if is_match else ("VALID_DIFF" if is_valid else "FAIL")
                followup_tag = "[Follow-Up]" if is_followup else "[Root]"
                print(f"[{turn_counter:02d}/{total_turns:02d}] [{status_flag}] {followup_tag} {q_text}")
                print(f"       SQL: {executed_sql[:80]}...")
                print(f"       Latency: {elapsed_ms:.2f} ms | Error: {error_msg}")

                details.append({
                    "id": t_id,
                    "question": q_text,
                    "follow_up": is_followup,
                    "valid": is_valid,
                    "match": is_match,
                    "latency_ms": round(elapsed_ms, 2),
                    "sql": executed_sql,
                    "error": error_msg
                })

    finally:
        chains_module.execute_query_readonly = orig_exec
        chains_module.analyze_explain_plan = orig_explain

    validity_rate = (sql_valid_count / total_turns) * 100.0
    accuracy_rate = (accuracy_match_count / total_turns) * 100.0
    avg_latency = sum(latencies_ms) / len(latencies_ms) if latencies_ms else 0.0
    sorted_lat = sorted(latencies_ms)
    p95_idx = int(len(sorted_lat) * 0.95)
    p95_latency = sorted_lat[min(p95_idx, len(sorted_lat) - 1)] if sorted_lat else 0.0

    metrics = {
        "mode": "mock" if use_mock else "gemini",
        "model": settings.GEMINI_MODEL if not use_mock else "FakeListChatModel",
        "total_turns": total_turns,
        "valid_queries": sql_valid_count,
        "validity_rate": round(validity_rate, 2),
        "accurate_queries": accuracy_match_count,
        "accuracy_rate": round(accuracy_rate, 2),
        "average_latency_ms": round(avg_latency, 2),
        "p95_latency_ms": round(p95_latency, 2),
        "details": details
    }

    print("\n" + "=" * 75)
    print("END-TO-END GEMINI LLM BENCHMARK COMPLETE:")
    print(f"  Total Evaluated Turns : {total_turns}")
    print(f"  SQL Validity Rate     : {validity_rate:.1f}% ({sql_valid_count}/{total_turns})")
    print(f"  Execution Accuracy    : {accuracy_rate:.1f}% ({accuracy_match_count}/{total_turns})")
    print(f"  Average Latency (E2E) : {avg_latency:.2f} ms (includes LLM generation)")
    print(f"  P95 Latency (E2E)     : {p95_latency:.2f} ms")
    print("=" * 75)

    save_llm_benchmark_markdown(metrics)
    return metrics


def save_llm_benchmark_markdown(metrics: Dict[str, Any]) -> None:
    """Writes actual measured LLM benchmark metrics to eval/results_llm.md."""
    out_dir = Path("eval")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "results_llm.md"

    md = f"""# End-to-End LLM Text-to-SQL Benchmark Results

This document records the **actual measured performance and execution accuracy**
from running the conversational pipeline (`eval/benchmark_llm.py`) with chained follow-ups.

---

## Executive Summary Metrics

| Metric | Result | Description |
| :--- | :--- | :--- |
| **LLM Model** | `{metrics['model']}` | Evaluated chat model |
| **Benchmark Mode** | `{metrics['mode'].upper()}` | Execution mode (`GEMINI` live or `MOCK` test harness) |
| **Total Conversational Turns** | `{metrics['total_turns']}` | Multi-turn queries evaluated across conversational chains |
| **Execution Accuracy** | **`{metrics['accuracy_rate']}%`** | Exact result-set match against gold query |
| **SQL Validity Rate** | **`{metrics['validity_rate']}%`** | Syntactically and semantically valid SQL execution |
| **Average End-to-End Latency** | **`{metrics['average_latency_ms']} ms`** | Full roundtrip time including LLM generation, RAG, guard, DB, and answer |
| **P95 Latency** | **`{metrics['p95_latency_ms']} ms`** | 95th percentile end-to-end response latency |

---

## Detailed Conversational Turn Results

| Turn ID | Follow-Up? | Question | Status | Latency |
| :---: | :---: | :--- | :---: | :---: |
"""

    for item in metrics["details"]:
        status_badge = "PASS" if item["match"] else ("VALID_DIFF" if item["valid"] else "FAIL")
        is_fu = "Yes" if item["follow_up"] else "No"
        md += f"| {item['id']} | {is_fu} | {item['question']} | {status_badge} | {item['latency_ms']} ms |\n"

    md += """
---

## Benchmark Reproducibility

To re-run with live Google Gemini:
```bash
export GOOGLE_API_KEY=your_gemini_api_key
python eval/benchmark_llm.py
```

To re-run offline verification harness:
```bash
python eval/benchmark_llm.py --mock
```
"""

    out_file.write_text(md, encoding="utf-8")
    print(f"Saved real LLM benchmark results to: {out_file.resolve()}")


def main():
    parser = argparse.ArgumentParser(description="End-to-End LLM Benchmark for Text-to-SQL")
    parser.add_argument("--mock", action="store_true", help="Run offline simulation using mock LLM")
    args = parser.parse_args()

    asyncio.run(run_llm_benchmark(use_mock=args.mock))


if __name__ == "__main__":
    main()
