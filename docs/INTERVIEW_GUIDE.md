# Text-to-SQL Enterprise RAG Chatbot: Interview Preparation Guide

This guide prepares you to explain the architecture, design decisions, and trade-offs of this system in technical interviews.

---

## Module 1: AST SQL Security Guard (`src/sql_guard.py`)

### What It Does
- Implements a multi-layered security gate before any AI-generated SQL query touches the database.
- Pre-parser regex layer strips/denies SQL comments (`--`, `#`, `/* */`), blocks semicolon-stacked queries, and filters out high-risk file export keywords.
- Parses SQL into an Abstract Syntax Tree (AST) using `sqlglot` under the `mysql` dialect.
- Validates that the root query is strictly a `SELECT` or `UNION`.
- Recursively traverses all AST nodes to reject DDL/DML constructs (`Insert`, `Update`, `Delete`, `Drop`, `Alter`, `Truncate`, `Into`, `Lock`, `Grant`).
- Blocks dangerous execution/delay functions (`SLEEP`, `BENCHMARK`, `LOAD_FILE`, `GET_LOCK`, `VERSION`).
- Extracts all table references (properly ignoring CTE aliases) and verifies them against an allowlist of introspected business tables while strictly denying internal tables (`users`, `chat_logs`) and system catalogs (`information_schema`, `mysql`, `sys`).
- Clamps outer query `LIMIT` to <= 50 (injecting `LIMIT 50` if missing) and clamps excessive `OFFSET`.

### Why It Was Designed That Way
- **Regex vs. AST Trade-off**: Regex alone is vulnerable to evasion (mixed case, nested parentheses, white space variations, comments). AST parsing alone can sometimes be fooled by parser lenient modes or multi-statement ambiguities. Combining a strict pre-parser regex defense with a fail-closed AST parser provides defense-in-depth.
- **Fail-Closed Principle**: If `sqlglot` fails to parse a statement or encounters an unrecognized construct, the query is immediately rejected rather than executed.

### 3 Tough Interview Questions & Model Answers
1. **Q: Why not rely on regex alone to detect `DROP` or `DELETE` statements?**  
   *Answer*: Regex cannot reliably understand SQL grammar. For instance, a benign query searching for a product named "Drop Cloth" (`WHERE product_name LIKE '%drop%'`) could be falsely blocked by a naive regex, while an attacker could bypass regex filters using comment injection, whitespace trickery, or hexadecimal encodings. An AST parser builds a semantic tree of operators, clauses, and identifiers, allowing us to inspect the actual statement type and function nodes regardless of surface syntax.
2. **Q: How does your guard handle CTEs (Common Table Expressions) and table aliases?**  
   *Answer*: In a query like `WITH top_orders AS (SELECT customer_id FROM orders) SELECT * FROM top_orders`, `top_orders` is a temporary alias, not a physical table. If we checked every identifier blindly against our database allowlist, `top_orders` would be rejected as an unauthorized table. The guard explicitly identifies CTE aliases from `exp.CTE` nodes and subquery aliases, removes them from the set of external physical tables, and verifies that the underlying tables (`orders`) are in the allowlist.
3. **Q: What happens if the LLM generates a subquery that accesses `users` inside an allowed table query?**  
   *Answer*: The guard performs a deep AST traversal using `ast.find_all(exp.Table)`. This traverses every subquery, CTE, derived table, JOIN, and UNION branch. Regardless of nesting depth, any reference to `users` or `chat_logs` triggers an immediate `SQLGuardError(code="BLOCKED_INTERNAL_TABLE")`.

---

## Module 2: Dual Database Engines & Connection Pooling (`src/database.py`)

### What It Does
- Employs two physically separated SQLAlchemy `AsyncEngine` instances:
  1. **App Engine (`app_engine`)**: Connects using application credentials (`DB_USER`) with read/write access to persist `users` and `chat_logs`.
  2. **Query Engine (`query_engine`)**: Connects using a dedicated least-privilege user (`chatbot_readonly`) with `SELECT`-only privileges on business tables.
- Hardens the query engine connections with a SQLAlchemy `connect` event listener (`@event.listens_for(query_engine.sync_engine, "connect")`) that executes each statement separately: `SET SESSION TRANSACTION READ ONLY;` and `SET SESSION max_execution_time=5000;`, avoiding multi-statement execution errors.
- Configures explicit connection pooling parameters (`pool_size=10`, `max_overflow=20`, `pool_recycle=1800`, `pool_pre_ping=True`).
- Exposes real-time pool metrics (`checked_in`, `checked_out`, `overflow`) via `get_pool_status()` for the `GET /api/health` endpoint.

### Why It Was Designed That Way
- **Defense-in-Depth against Guard Bypass**: Even if an attacker discovered a 0-day parser evasion in `sqlglot` or bypassed application logic, the database engine itself rejects any write or catalog query because the MySQL user literally lacks the permissions and the MySQL connection session is forced into `READ ONLY` mode.
- **Connection Isolation**: Long-running analytical AI queries cannot starve the transactional application connection pool used for user logins and chat persistence.

### 3 Tough Interview Questions & Model Answers
1. **Q: Why use two separate database engines instead of switching transactions on a single engine?**  
   *Answer*: Using a single engine with shared credentials means any connection has the theoretical authority to mutate tables if an application bug occurs. By using dual engines, we enforce least privilege at the operating system and database network boundary: the query engine authenticates as a user that MySQL has strictly forbidden from writing or touching user credential tables. Additionally, having independent connection pools prevents slow analytical queries from monopolizing connections needed for fast user authentication.
2. **Q: What is the purpose of `pool_pre_ping=True` and `pool_recycle=1800`?**  
   *Answer*: MySQL drops idle connections after `wait_timeout` (default 8 hours, often shorter behind firewalls/proxies). Without `pool_recycle`, an application attempting to reuse a closed connection would encounter `OperationalError: MySQL server has gone away`. `pool_recycle=1800` preemptively recycles connections older than 30 minutes, and `pool_pre_ping=True` issues a lightweight `SELECT 1` heartbeat before checkout to ensure the socket is alive.
3. **Q: How does `max_execution_time` protect the database?**  
   *Answer*: If the LLM generates an accidental Cartesian product (e.g. unindexed CROSS JOIN between large tables), it could peg MySQL CPU to 100% and exhaust server memory. Setting `SET SESSION max_execution_time=5000` instructs the MySQL query executor to terminate any query that runs longer than 5 seconds, protecting production availability.

---

## Module 3: Asynchronous Backend & Authentication (`server.py`, `src/auth.py`)

### What It Does
- Exposes high-performance async endpoints using FastAPI, ASGI, and `aiomysql`.
- Authenticates requests using JSON Web Tokens (JWT) signed with HMAC-SHA256.
- Enforces strict startup validation: refuses to boot if `JWT_SECRET` is unset or less than 32 characters long.
- Uses `bcrypt` for one-way salted password hashing.
- Enforces strict Pydantic input models for username character whitelist (`^[a-zA-Z0-9_\-\.]+$`), password length (8-128 chars), and question length bounds (1-1000 chars).
- Enforces strict tenant data isolation: queries to `/api/history` filter explicitly on `WHERE user_id = :uid`.

### Why It Was Designed That Way
- **Async Event Loop**: Text-to-SQL applications spend 90%+ of their execution time waiting on network I/O (LLM API responses, vector search, database queries). Synchronous endpoints would block OS threads, limiting throughput. Async endpoints allow a single process to handle hundreds of concurrent requests efficiently.
- **Fail-Fast Configuration**: Bootstrapping fails immediately if weak secrets are detected, preventing insecure configurations from reaching staging or production.

### 3 Tough Interview Questions & Model Answers
1. **Q: Why should synchronous database introspection not be run inside an async route?**  
   *Answer*: Synchronous blocking I/O halts Python's single-threaded `asyncio` event loop, pausing all other concurrent coroutines, websocket connections, and HTTP requests. In our system, the synchronous `SQLDatabase` introspection runs once during lifespan startup inside `asyncio.to_thread` to ensure the main thread is never blocked.
2. **Q: How does your system prevent JWT signature forgery?**  
   *Answer*: We enforce a cryptographically secure `JWT_SECRET` (minimum 256 bits / 32 characters) validated at boot time. During verification (`decode_access_token`), we explicitly specify `algorithms=["HS256"]` to defend against the classic "None algorithm" signature bypass attack.
3. **Q: How is user privacy guaranteed across conversation logs?**  
   *Answer*: The `chat_logs` table has a mandatory `user_id` foreign key. The `/api/history` and `/api/chat` endpoints resolve `user_id` solely from the cryptographically verified JWT `sub` claim (via FastAPI `Depends(get_current_user)`). The client cannot pass or tamper with `user_id` in the request body.

---

## Module 4: Dynamic RAG Pipeline with ChromaDB (`src/rag.py`)

### What It Does
- Chunks introspected database schema into individual per-table vector documents containing DDL, column types, foreign key relationships, and representative sample data.
- Loads 18+ enterprise few-shot demonstration question->SQL pairs from `data/examples.json`.
- Stores embeddings in persistent ChromaDB collections using Google Gemini embeddings (`models/gemini-embedding-001`, 3072 dimensions, replacing deprecated `text-embedding-004`) with deterministic fallback for test environments.
- On startup, computes a SHA-256 hash of the introspected schema AND the embedding model name. If schema or model drift occurs, it deletes existing collections and rebuilds the vector index cleanly. If `GOOGLE_API_KEY` is set but indexing fails, it fails loudly without silent fallbacks.
- Retrieves top-k relevant tables and top-k few-shot examples for each incoming natural language question and formats them into the prompt.

### Why It Was Designed That Way
- **Token Efficiency & Context Window**: In enterprise databases with 50-200 tables, dumping the entire database schema into the LLM prompt blows up context window tokens, increases latency, increases inference cost, and confuses the model. RAG dynamic retrieval injects only the 3-5 relevant tables needed to answer the user's specific query.
- **Few-Shot In-Context Learning**: Demonstrating domain-specific JOIN patterns and date filtering syntax significantly improves the LLM's first-shot generation accuracy.

### 3 Tough Interview Questions & Model Answers
1. **Q: Why use schema chunking instead of passing the entire database DDL to Gemini?**  
   *Answer*: Full DDL injection scales poorly as the database grows: a 100-table schema consumes tens of thousands of prompt tokens per request, degrading latency and inference cost. Moreover, LLMs suffer from "lost in the middle" phenomena when presented with massive extraneous context. RAG schema retrieval extracts only the relevant sub-schema, keeping prompts compact, fast, and focused.
2. **Q: How does your RAG index stay synchronized when database migrations occur?**  
   *Answer*: We implemented hash-based cache invalidation. On startup, `SchemaRAGIndex` hashes the table definitions along with the embedding model name and compares the digest against the stored schema hash. If a table has been added, columns modified, or model changed, the index drops the stale vector collections and re-indexes the new schema chunks.
3. **Q: Why persist few-shot examples in a vector store instead of hardcoding them in the prompt?**  
   *Answer*: A static list of 20 few-shot examples in every prompt adds unnecessary token bloat. By vector indexing the examples, we retrieve only the 2-3 most semantically similar examples (e.g. retrieving JOIN examples when the user asks a relational query, and aggregation examples when they ask for metrics).

---

## Module 5: Query Optimization, EXPLAIN Plans & Self-Correction (`src/optimizer.py`, `src/chains.py`)

### What It Does
- **Prompt Engineering**: Enforces rules forbidding `SELECT *`, mandating explicit column selections, requiring explicit JOIN keys, pushing aggregations to MySQL, and mandating a row LIMIT.
- **AST Optimization**: Uses `sqlglot.optimizer.optimize` with introspected schema to qualify columns, simplify boolean/arithmetic expressions, and remove redundant projections.
- **Defense-in-Depth Pipeline**: The candidate SQL is guarded, passed to `optimize_query(schema=introspected_schema)`, re-guarded through `guard.validate_and_sanitize`, evaluated via `analyze_explain_plan(query_engine, ...)`, and then executed.
- **EXPLAIN Analysis**: Before running the query, the engine runs `EXPLAIN <query>`. It parses the access type (`type='ALL'`) and estimated rows. If estimated rows exceed `MAX_EXPLAIN_ROWS` (default 10,000) during a full table scan, it flags or rejects the query.
- **Self-Correction Retry Loop**: If query execution fails at the database level, the error message and failed SQL are fed back into Gemini via `CORRECTION_PROMPT`, retrying up to 2 times and passing through the security guard and EXPLAIN check on each retry.
- **Conversational Memory**: Utilizes `MessagesPlaceholder(variable_name="chat_history")` to inject the last 3 turns so contextual follow-ups ("Which of those were completed?") work seamlessly.

### Why It Was Designed That Way
- **Single Generation**: The original codebase had a bug where `sql_chain` was invoked once in `server.py` and then invoked a second time inside `full_chain`. We refactored the pipeline to generate SQL exactly once, guard it, optimize it, execute it, and reuse that exact sanitized SQL.
- **Defensible "Optimized" Claim**: Claiming "optimized query generation" requires concrete evidence: AST qualification, EXPLAIN plan inspection, full scan detection, and self-correction loops.

### 3 Tough Interview Questions & Model Answers
1. **Q: How does the self-correction loop work without creating an infinite loop?**  
   *Answer*: The loop is bounded by `max_retries=2` (3 total attempts). If a query fails with a database syntax or column error, the error message is fed back to the LLM. Crucially, security guard violations (e.g. attempting to query `users` or execute a `DROP`) fail closed immediately and are *never* retried, preventing adversarial prompt injection retries.
2. **Q: How does your EXPLAIN plan analyzer identify potential production bottlenecks?**  
   *Answer*: `analyze_explain_plan` issues `EXPLAIN <query>` on the read-only query engine. It inspects the `type` column: if the access method is `ALL` (full table scan) and the estimated rows exceed `MAX_EXPLAIN_ROWS`, it flags the query as unsafe, preventing unindexed table scans on large tables from exhausting I/O.
3. **Q: How did you fix the double-generation bug in LangChain?**  
   *Answer*: In early implementations, `RunnablePassthrough.assign(query=sql_chain)` in `full_chain` caused LangChain to re-run the entire SQL generation LLM prompt when synthesizing the answer. We decoupled generation into explicit modular steps: candidate SQL is generated once, guarded and sanitized, executed once, and passed directly into the answer synthesis chain.
