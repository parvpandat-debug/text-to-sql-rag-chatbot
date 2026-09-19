# Resume Evidence & Claim Verification Guide

This document maps each bullet point claim on your resume to the **exact implementation files, core classes/functions, and automated tests** that prove the claim is 100% true, defensible, and verified in code.

---

## Resume Claim Mapping Table

| # | Resume Claim | Implementation File(s) | Primary Functions & Classes | Verification / Proof Tests |
| :---: | :--- | :--- | :--- | :--- |
| **1** | *"Architected a Text-to-SQL RAG pipeline using LangChain and Google Gemini"* | [`../src/rag.py`](../src/rag.py)<br>[`../src/chains.py`](../src/chains.py)<br>[`../data/examples.json`](../data/examples.json) | `SchemaRAGIndex`<br>`get_embedding_model`<br>`build_table_documents`<br>`retrieve_relevant_context`<br>`execute_rag_pipeline` | [`../tests/test_rag.py`](../tests/test_rag.py) (8 tests: `test_google_api_key_uses_gemini_embedding_model`, `test_rag_collection_drop_before_rebuild`, `test_schema_hash_includes_model_name`, `test_rag_loud_failure_when_google_api_key_set_and_indexing_fails`, etc.)<br>[`../tests/test_api.py::test_chat_happy_path`](../tests/test_api.py) |
| **2** | *"Enabled natural language to optimized MySQL query conversion using dynamic schema introspection"* | [`../src/optimizer.py`](../src/optimizer.py)<br>[`../src/database.py`](../src/database.py)<br>[`../src/chains.py`](../src/chains.py) | `get_introspected_business_tables`<br>`get_introspected_schema_dict`<br>`optimize_query`<br>`analyze_explain_plan`<br>`SQL_SYSTEM_PROMPT` rules | [`../tests/test_optimizer.py`](../tests/test_optimizer.py) (7 tests: `test_optimize_query_simplifies_and_formats`, `test_optimize_query_with_schema`, `test_get_introspected_schema_dict_caching`, `test_explain_plan_detects_massive_full_table_scan`, `test_pipeline_wires_optimizer_and_explain`, etc.)<br>[`../eval/benchmark.py`](../eval/benchmark.py) (Guard Regression Check: 100% validity over 30 queries) |
| **3** | *"Engineered an AST-based SQL security guard using sqlglot and regex-based validation"* | [`../src/sql_guard.py`](../src/sql_guard.py) | `SQLSecurityGuard`<br>`_regex_defense_layer`<br>`validate_and_sanitize`<br>`_extract_and_validate_tables`<br>`SQLGuardError` | [`../tests/test_sql_guard.py`](../tests/test_sql_guard.py) (53 distinct security unit tests covering DDL, comments, stacked SQL, evasion functions, CTE isolation, and LIMIT clamping) |
| **4** | *"Enforced 100% read-only query execution with automated row-limit clamping (<=50 rows) and table-level isolation"* | [`../src/sql_guard.py`](../src/sql_guard.py)<br>[`../src/database.py`](../src/database.py)<br>[`../scripts/create_readonly_user.sql`](../scripts/create_readonly_user.sql) | `_clamp_limits`<br>`execute_query_readonly`<br>`query_engine` (`connect` listener: `SET SESSION TRANSACTION READ ONLY` & `SET SESSION max_execution_time`) | [`../tests/test_sql_guard.py::test_valid_select_injects_limit`](../tests/test_sql_guard.py)<br>[`../tests/test_readonly.py`](../tests/test_readonly.py) (7 tests: `test_connect_hook_fails_closed_on_cursor_error`, `test_readonly_fixture_fails_in_ci_if_connection_unavailable`, plus 5 MySQL integration tests: INSERT/DROP fail, Error 1142 on users, query timeout abort) |
| **5** | *"Built a secure, asynchronous backend using FastAPI with JWT authentication and SQLAlchemy connection pooling"* | [`../server.py`](../server.py)<br>[`../src/auth.py`](../src/auth.py)<br>[`../src/config.py`](../src/config.py)<br>[`../src/database.py`](../src/database.py) | `app_engine` / `query_engine`<br>`get_pool_status`<br>`create_access_token`<br>`decode_access_token`<br>`AuthRequest` validation | [`../tests/test_auth.py`](../tests/test_auth.py) (17 tests: auth flows + missing `JWT_SECRET` / `QUERY_DB_PASSWORD` startup failures)<br>[`../tests/test_api.py`](../tests/test_api.py) (11 tests: `test_api_health`, `test_missing_token_returns_401`, `test_chat_follow_up_uses_history`, `test_lifespan_loud_failure_when_google_api_key_set`, `test_lifespan_fallback_when_no_google_api_key`) |
| **6** | *"Validated reliability through automated Pytest integration suites covering security, auth, and API endpoints"* | [`../tests/`](../tests/)<br>[`../.github/workflows/test.yml`](../.github/workflows/test.yml)<br>[`../docker-compose.yml`](../docker-compose.yml) | 103 automated tests across 6 test suites running on in-memory SQLite and MySQL 8.0 CI | **103 Total Automated Tests**:<br>- 53 guard security tests (`tests/test_sql_guard.py`)<br>- 17 auth/JWT & config tests (`tests/test_auth.py`)<br>- 11 API & lifespan tests (`tests/test_api.py`)<br>- 8 RAG & embedding tests (`tests/test_rag.py`)<br>- 7 optimizer & EXPLAIN tests (`tests/test_optimizer.py`)<br>- 7 read-only database isolation & connect hook tests (`tests/test_readonly.py`) |

---

## Technical Defensibility Details

### 1. RAG Architecture
- **Claim**: We chunk the database schema table by table into individual vector embeddings rather than dumping the whole database DDL into a static prompt.
- **Evidence**: [`../src/rag.py`](../src/rag.py) `build_table_documents()` extracts table schemas, columns, and sample rows dynamically. ChromaDB indexes these documents using current Google Gemini embeddings (`models/gemini-embedding-001`, 3072 dimensions, replacing the deprecated `text-embedding-004`). When a user asks a question, only top-k relevant tables and top-k few-shot examples from [`../data/examples.json`](../data/examples.json) are retrieved and injected into the prompt.
- **Cache Invalidation & Model Drift**: Computes a SHA-256 hash across table structures AND the embedding model name (`compute_schema_hash`). If table schemas change or the embedding model changes, the vector store drops the collections and re-indexes on startup. If `GOOGLE_API_KEY` is configured but indexing fails, startup fails loudly without silent fallbacks.

### 2. Multi-Layer Defense-in-Depth Security
- **Claim**: 100% read-only and hardened against injection attacks.
- **Evidence**:
  1. **Layer 1 (Regex Pre-Parser)**: Blocks SQL comments (`--`, `#`, `/* */`), semicolon-stacked queries, and file-write keywords before AST parsing begins.
  2. **Layer 2 (Fail-Closed AST Guard)**: Parses with `sqlglot` using `dialect="mysql"`. Any syntax anomaly or unrecognized statement rejects the query immediately.
  3. **Layer 3 (Node Tree Traversal)**: Traverses the complete AST; rejects non-SELECT root queries, DDL, DML, file operations (`INTO OUTFILE/DUMPFILE`), and dangerous functions (`SLEEP`, `BENCHMARK`, `LOAD_FILE`, `GET_LOCK`).
  4. **Layer 4 (Table Allowlist & CTE Isolation)**: Collects CTE aliases; verifies every real table against introspected business tables; strictly blocks system schemas (`information_schema`, `mysql`, `sys`) and application tables (`users`, `chat_logs`).
  5. **Layer 5 (Automated Row-Limit Clamping)**: Inspects AST `Limit` and `Offset`; injects `LIMIT 50` if absent, clamps any value > 50 down to 50.
  6. **Layer 6 (Session & User Read-Only Database Isolation)**: Chatbot query execution runs under dedicated `chatbot_readonly` user ([`../scripts/create_readonly_user.sql`](../scripts/create_readonly_user.sql)) with a SQLAlchemy `connect` event executing separate statements: `SET SESSION TRANSACTION READ ONLY;` and `SET SESSION max_execution_time=5000;`. Fails closed if session hardening fails.

### 3. Asynchronous Connection Pooling & Health Monitoring
- **Claim**: Built a high-performance async backend with SQLAlchemy pooling and live diagnostic metrics.
- **Evidence**:
  - `server.py` and `src/database.py` use `create_async_engine` with `aiomysql`.
  - Dual engines separate transactional auth writes from analytical query reads.
  - `GET /api/health` exposes real-time connection pool metrics: `size`, `checked_in`, `checked_out`, and `overflow`.

### 4. Reproducible Evaluation Suites
- **Guard Regression Check** ([`../eval/benchmark.py`](../eval/benchmark.py)): 30 enterprise queries across 6 categories verifying AST guard clamping, query optimizer passes, 0 false positive guard rejections, and execution equivalence against SQLite/MySQL ground truth in < 6 ms average latency.
- **End-to-End LLM Benchmark** ([`../eval/benchmark_llm.py`](../eval/benchmark_llm.py)): Multi-turn conversational benchmark with chained follow-ups measuring real end-to-end latency including Gemini generation, RAG context retrieval, EXPLAIN execution, and response synthesis.
