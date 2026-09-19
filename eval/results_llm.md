# End-to-End LLM Text-to-SQL Benchmark Results

This document records the **actual measured performance and execution accuracy**
from running the conversational pipeline (`eval/benchmark_llm.py`) with chained follow-ups.

---

## Executive Summary Metrics

| Metric | Result | Description |
| :--- | :--- | :--- |
| **LLM Model** | `FakeListChatModel` | Evaluated chat model |
| **Benchmark Mode** | `MOCK` | Execution mode (`GEMINI` live or `MOCK` test harness) |
| **Total Conversational Turns** | `10` | Multi-turn queries evaluated across conversational chains |
| **Execution Accuracy** | **`100.0%`** | Exact result-set match against gold query |
| **SQL Validity Rate** | **`100.0%`** | Syntactically and semantically valid SQL execution |
| **Average End-to-End Latency** | **`68.03 ms`** | Full roundtrip time including LLM generation, RAG, guard, DB, and answer |
| **P95 Latency** | **`131.47 ms`** | 95th percentile end-to-end response latency |

---

## Detailed Conversational Turn Results

| Turn ID | Follow-Up? | Question | Status | Latency |
| :---: | :---: | :--- | :---: | :---: |
| C1-T1 | No | Show all customers living in New York. | PASS | 131.47 ms |
| C1-T2 | Yes | Which of those signed up after March 2025? | PASS | 51.16 ms |
| C1-T3 | Yes | How many total customers is that? | PASS | 64.44 ms |
| C2-T1 | No | Show orders placed by customer 1 sorted by date descending. | PASS | 61.78 ms |
| C2-T2 | Yes | Which of those orders were completed? | PASS | 59.29 ms |
| C2-T3 | Yes | How much did customer 1 spend in total on those completed orders? | PASS | 65.24 ms |
| C3-T1 | No | List all products in the Electronics category. | PASS | 62.69 ms |
| C3-T2 | Yes | Find products among those priced under 100 dollars. | PASS | 59.01 ms |
| C4-T1 | No | Calculate the total revenue generated from all completed orders. | PASS | 66.04 ms |
| C4-T2 | No | Count how many orders exist in each status. | PASS | 59.23 ms |

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
