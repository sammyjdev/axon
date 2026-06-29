# dec-121 Phase 2 — Retire Redis (port dep-graph to Postgres) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Redis from AXON entirely by porting the one live consumer of the Redis `dep:*` call-graph (the `get_dependencies` MCP tool) to a Postgres `symbol_deps` table, deleting the dead `subgraph:*` cache / `traverse` / batch code, fixing the `graph_source.py` cache-invalidation bug under Postgres, and dropping the `redis` dependency.

**Architecture:** The Redis `GraphStore` (`src/axon/store/graph_store.py`) holds two things: (1) a LIVE `dep:<symbol> → {calls, called_by}` call-graph read by exactly one MCP tool (`get_dependencies` via `store.get_subgraph`), written by the indexer (`pipeline.py`) and 4 CLI index commands; and (2) a DEAD `subgraph:<id>` TTL cache + `traverse()` + `upsert_deps_batch()` with zero production readers. Phase 2 ports (1) to a small Postgres-backed `symbol_deps` table behind a `get_subgraph`/`upsert_deps`-compatible interface, deletes (2) outright, fixes the in-memory GLYPH cache invalidation that currently keys on the SQLite WAL mtime (a no-op under Postgres), and removes Redis from config + deps. GLYPH graph retrieval (nodes/edges, dec-116/117) is unaffected — that is a separate concern already on Postgres.

**Tech Stack:** Python 3.11+, `asyncpg` + Postgres, Typer (`pb` CLI), pytest + `testcontainers.postgres`. Removes `redis` and the `testcontainers[redis]` extra. No new dependencies.

## Global Constraints

- The ONLY behaviour to preserve is the `get_dependencies` MCP tool: given a symbol it returns its `calls` and `called_by` lists (or "no deps"). Its output format is unchanged.
- The new store is Postgres-backed, table `symbol_deps(symbol text PRIMARY KEY, calls jsonb NOT NULL, called_by jsonb NOT NULL)`, upsert via `INSERT ... ON CONFLICT (symbol) DO UPDATE`. Follow the existing `PostgresGraphRepository` style (`src/axon/store/pg_graph_repository.py`): `asyncpg` pool, `ensure_schema()` inline DDL, jsonb codec.
- The new store exposes the SAME method names the callers use today so the repoint is mechanical: `async upsert_deps(symbol, *, calls, called_by)`, `async get_subgraph(symbol) -> {"exists": bool, "calls": list, "called_by": list}`, `async connect()` (no-op for parity), `async close()`.
- DELETE (zero production callers — verified in the Phase-2 investigation): the `subgraph:*` cache methods (`cache_subgraph`, `get_cached_subgraph`, `invalidate`), `traverse`, `upsert_deps_batch`, `set_calls`, `set_called_by`, `get_calls`, `get_called_by` — keep only what `upsert_deps` + `get_subgraph` need. The `git_event.py` `invalidate()` call is a no-op (nothing writes `subgraph:*`) — remove it.
- `graph_source.py`'s in-memory `_GRAPH_CACHE` currently invalidates on the SQLite WAL file mtime (`_db_mtime`), which never changes under Postgres → the cache would never refresh. Replace the invalidation signal so it is correct when `graph_backend == "postgres"` (the default).
- Out of scope (UNTOUCHED): the relational SQLite repositories (Phase 3), the structural `nodes`/`edges` graph + GLYPH retrieval (already PG, dec-116/117), the vector store (Phase 1, done).
- `redis` is removed from `pyproject.toml` and the `testcontainers[...]` extra; runtime `redis_url` config is removed. Add a guard test mirroring `tests/test_no_qdrant.py`.
- Validation prefixes with `rtk`. Export `AXON_PG_URL=postgresql://axon:axon@localhost:5434/axon` for test runs. `asyncio_mode="auto"`. Stage only each task's named files (never `git add -A`).

---

### Task 1: Postgres `symbol_deps` store

**Files:**
- Create: `src/axon/store/pg_symbol_deps.py`
- Test: `tests/store/test_pg_symbol_deps.py`

**Interfaces:**
- Produces: `class PostgresSymbolDeps` with `__init__(dsn: str)`, `async ensure_schema()`, `async connect()` (no-op, parity with the old `GraphStore`), `async upsert_deps(symbol: str, *, calls: list[str], called_by: list[str]) -> None`, `async get_subgraph(symbol: str) -> dict` returning `{"exists": bool, "calls": list[str], "called_by": list[str]}`, `async close()`.

- [ ] **Step 1: Read the reference pattern.** Read `src/axon/store/pg_graph_repository.py` for the asyncpg pool + `ensure_schema` + jsonb-codec pattern, and `src/axon/store/graph_store.py:36-94` for the exact `upsert_deps` / `get_subgraph` semantics to mirror (`get_subgraph` returns `{"exists", "calls", "called_by"}`; `exists` is false when the symbol has no row).

- [ ] **Step 2: Write the failing test** (`tests/store/test_pg_symbol_deps.py`) — spin a `PostgresContainer("pgvector/pgvector:pg16", ...)` module fixture (copy the fixture shape from `tests/store/test_decision_backfill_executor.py`). Tests: (a) `upsert_deps` then `get_subgraph` returns the calls/called_by and `exists=True`; (b) `get_subgraph` of an unknown symbol returns `{"exists": False, "calls": [], "called_by": []}`; (c) a second `upsert_deps` for the same symbol overwrites (ON CONFLICT), not duplicates.

- [ ] **Step 3: Run it to verify it fails** — `rtk pytest tests/store/test_pg_symbol_deps.py -q` → FAIL (ModuleNotFoundError).

- [ ] **Step 4: Implement `PostgresSymbolDeps`** — `ensure_schema` creates `symbol_deps(symbol text PRIMARY KEY, calls jsonb NOT NULL DEFAULT '[]', called_by jsonb NOT NULL DEFAULT '[]')`; `upsert_deps` does `INSERT ... ON CONFLICT (symbol) DO UPDATE SET calls=excluded.calls, called_by=excluded.called_by`; `get_subgraph` selects the row and shapes the dict (register the jsonb codec like `pg_decision_repository._init_conn`). `connect()` is a no-op.

- [ ] **Step 5: Run it green** — `rtk pytest tests/store/test_pg_symbol_deps.py -q` → PASS (3 tests).

- [ ] **Step 6: Lint + commit** — `rtk ruff check src/axon/store/pg_symbol_deps.py tests/store/test_pg_symbol_deps.py`; commit `feat(store): Postgres symbol_deps store (ports the Redis dep-graph)`.

---

### Task 2: Repoint the indexer writer + CLI sites to `PostgresSymbolDeps`

**Files:**
- Modify: `src/axon/embedder/pipeline.py` (the `graph_store` param type + the `upsert_deps` call site ~308-310; the import at line 14)
- Modify: `src/axon/cli/pb.py` (the 4 `GraphStore(url=_RUNTIME.redis_url)` instantiations in the `index`, `index-dev`, `watch`, `scan` commands)
- Test: `tests/embedder/test_pipeline_symbol_deps.py` (or extend the existing pipeline test)

**Interfaces:**
- Consumes: `PostgresSymbolDeps` (Task 1). The pipeline already calls `graph_store.upsert_deps(symbol, calls=..., called_by=...)` — the method name matches, so the change is the construction + type hint, not the call.

- [ ] **Step 1: Find every construction site.** `rtk proxy grep -rn "GraphStore(" src/` — confirm the 4 CLI sites + any test fakes. Each constructs the Redis `GraphStore`; they become `PostgresSymbolDeps(dsn=_RUNTIME.pg_url)`.

- [ ] **Step 2: Write the failing test** — a pipeline test that indexes a small file with an injected `PostgresSymbolDeps` (real container) and asserts the symbol's deps are retrievable via `get_subgraph`. RED before the repoint if the pipeline still imports/expects the Redis type.

- [ ] **Step 3: Repoint** — in `pipeline.py`, change `from axon.store.graph_store import GraphStore` → `from axon.store.pg_symbol_deps import PostgresSymbolDeps` and the `graph_store: GraphStore | None` hint → `PostgresSymbolDeps | None`. In `pb.py`, change the 4 `GraphStore(url=_RUNTIME.redis_url)` → `PostgresSymbolDeps(dsn=_RUNTIME.pg_url)` and call `await store.ensure_schema()` where the old code called `connect()` (or keep `connect()` as the no-op + add an `ensure_schema()` call once per command).

- [ ] **Step 4: Run green** — the new pipeline test + `rtk pytest tests/embedder -q`.

- [ ] **Step 5: Lint + commit** — `feat(embedder,cli): write the call-graph to Postgres symbol_deps (not Redis)`.

---

### Task 3: Repoint the `get_dependencies` reader

**Files:**
- Modify: `src/axon/mcp/server.py` (`_get_graph_store()` ~line 91-95 and `get_dependencies` ~462-484)
- Test: `tests/mcp/test_get_dependencies_pg.py`

- [ ] **Step 1: Write the failing test** — seed a `symbol_deps` row (real container), call `get_dependencies("some_symbol")` with the PG store wired in, assert the output lists calls/called_by; and assert the "no deps" branch for an unknown symbol. (Mirror how `tests/mcp/test_retrieval_tools.py` fakes/injects stores.)

- [ ] **Step 2: Run it red.**

- [ ] **Step 3: Repoint** — `_get_graph_store()` builds `PostgresSymbolDeps(dsn=_RUNTIME.pg_url)` instead of `GraphStore(url=_REDIS_URL)`; `get_dependencies` keeps calling `store.get_subgraph(symbol)` (same shape) — only the factory changes. Remove the now-unused `_REDIS_URL` module global if nothing else uses it (grep first).

- [ ] **Step 4: Run green** — the new test + `rtk pytest tests/mcp -q`.

- [ ] **Step 5: Lint + commit** — `feat(mcp): get_dependencies reads the call-graph from Postgres symbol_deps`.

---

### Task 4: Fix the GLYPH graph cache invalidation under Postgres

**Files:**
- Modify: `src/axon/context/graph_source.py` (`_db_mtime` / `_GRAPH_CACHE` keying)
- Test: `tests/context/test_graph_source_cache_pg.py` (or extend `tests/context/test_graph_source.py`)

**Context:** `graph_source.py` caches the materialized GLYPH `NetworkXStore` keyed by `(db_path, mtime)` where `mtime` comes from the SQLite WAL file (`_db_mtime`). Under `graph_backend == "postgres"` (the default) there is no WAL file, so the mtime never changes and the cache never invalidates → stale graph reads after a re-index. Replace the invalidation signal for the Postgres backend.

- [ ] **Step 1: Write the failing test** — with the graph backend set to Postgres, materialize the GLYPH graph, add a node via the PG graph repo, and assert the next `graph_source` read reflects the new node (i.e. the cache invalidated). RED today because the mtime key never changes.

- [ ] **Step 2: Run it red.**

- [ ] **Step 3: Implement a backend-correct invalidation signal** — when `graph_backend == "postgres"`, key the cache on a cheap monotonic signal from Postgres instead of the WAL mtime: either `MAX(updated_at)`/a row-count+max-id over `nodes`+`edges`, or a dedicated `SELECT pg_catalog.txid_snapshot...`/sequence. Simplest robust choice: key on `(node_count, edge_count, max(node_id))` queried once per call (cheap at AXON scale). Keep the SQLite WAL path for `graph_backend == "sqlite"` until Phase 3 removes it. Read the current `_db_mtime`/`_build_glyph_graph` (around `graph_source.py:159-185`) before editing.

- [ ] **Step 4: Run green** — new test + `rtk pytest tests/context -q`.

- [ ] **Step 5: Lint + commit** — `fix(context): invalidate the GLYPH graph cache on Postgres state, not SQLite WAL mtime`.

---

### Task 5: Delete the Redis `GraphStore` and its dead code

**Files:**
- Delete: `src/axon/store/graph_store.py`
- Delete: `tests/store/test_graph_cache.py`, `tests/store/test_upsert_deps_batch.py` (cover deleted dead code — verify first)
- Modify: `src/axon/hooks/git_event.py` (remove the no-op `graph.invalidate(symbol.id)` call ~line 88 and the `GraphStore` construction feeding it)
- Modify: any remaining `from axon.store.graph_store import` importer flagged by grep

- [ ] **Step 1: Prove `graph_store` is unreferenced** — `rtk proxy grep -rn "from axon.store.graph_store import\|graph_store import GraphStore\|GraphStore(" src/ tests/`. After Tasks 2–3 the only hits should be `git_event.py` + dead-code tests. Repoint/remove each (git_event's call is a no-op — delete it and the construction).

- [ ] **Step 2: Delete** the module + the two dead-code tests (`git rm`).

- [ ] **Step 3: Verify** — `rtk python3 -m compileall src/axon` clean; `rtk pytest tests/store tests/hooks -q` green; no `ModuleNotFoundError: graph_store`.

- [ ] **Step 4: Commit** — `feat(store): delete the Redis GraphStore (dep-graph ported to Postgres; subgraph cache was dead)`.

---

### Task 6: Drop the `redis` dependency + config and add the guard

**Files:**
- Modify: `src/axon/config/runtime.py` (remove `redis_url` field + its `REDIS_URL` read)
- Modify: `src/axon/config/platform.py` (remove the `REDIS_URL=...` line from generated env)
- Modify: `pyproject.toml` (remove `redis`; change `testcontainers[redis,postgres]` → `testcontainers[postgres]`)
- Modify: `tests/mcp/test_axon_tools.py` (drop `redis` from the `axon_health` subsystem assertions), `tests/config/test_platform.py` (drop the `REDIS_URL` assertion), and any other `redis_url`/`REDIS_URL` test references flagged by grep
- Create: `tests/test_no_redis.py` (guard mirroring `tests/test_no_qdrant.py`: no `import redis`/`from redis` in `src/`+`scripts/`+`tests/` excl fixtures; `redis` absent from pyproject deps; `graph_store.py` deleted)

- [ ] **Step 1: Grep the blast radius** — `rtk proxy grep -rn "redis_url\|REDIS_URL\|import redis\|from redis" src/ scripts/ tests/`. (Lesson from Phase 1: a field/dep removal needs a REPO-WIDE grep including `scripts/`, not just the obvious files.) Enumerate every reader.

- [ ] **Step 2: Write the guard test** (`tests/test_no_redis.py`) and run it RED (redis still present).

- [ ] **Step 3: Remove** `redis_url` from runtime + platform env gen, drop the dep + testcontainers extra, and fix every test reference from Step 1 (the `axon_health` probe should stop listing redis once Redis is gone — update `server.py`'s health probe and its tests together).

- [ ] **Step 4: Run green** — `rtk pytest tests/test_no_redis.py tests/config tests/mcp -q` (with `AXON_PG_URL` set).

- [ ] **Step 5: Lint + commit** — `feat: drop the redis dependency + config (dep-graph is on Postgres now)`.

---

### Task 7: Operational verification + Redis teardown

**Files:** None (operational).

- [ ] **Step 1:** `rtk pytest tests/ -q` (with `AXON_PG_URL` set) — full suite green; no redis/qdrant imports leaked.
- [ ] **Step 2:** Re-index a repo (`pb graph index` / `pb index`) and confirm `symbol_deps` is populated in Postgres (`SELECT count(*) FROM symbol_deps;`).
- [ ] **Step 3:** Call the `get_dependencies` MCP tool (or its CLI equivalent) for a known symbol; confirm calls/called_by come back from Postgres with no Redis connection attempt.
- [ ] **Step 4:** Tear down the Redis container: `docker stop axon-redis-1 && docker rm axon-redis-1`. Confirm `pb doctor` + MCP still operate.
- [ ] **Step 5:** Update `docs/decisions/dec-121-postgres-unified-storage.md` — mark the Redis slice done; update the D4 note in `CLAUDE.md` (Redis cache row no longer applies).

---

## Self-Review

**Spec coverage:** port `dep:*` → Tasks 1–3 (`symbol_deps` store + writer + reader); delete dead `subgraph:*`/`traverse`/batch → Task 5; fix `graph_source` invalidation → Task 4; drop Redis dep/config + guard → Task 6; operational teardown → Task 7. GLYPH retrieval + relational SQLite untouched (Phase 3).

**Known follow-ups (out of scope):** Phase 3 (SQLite removal) — the `graph_source` SQLite-WAL path kept in Task 4 is removed there. The `symbol_deps` table is intentionally separate from the structural `nodes`/`edges` graph; unifying them is not in scope.
