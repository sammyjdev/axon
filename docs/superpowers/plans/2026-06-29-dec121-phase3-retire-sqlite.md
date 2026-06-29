# dec-121 Phase 3 — Retire SQLite (Postgres-only relational) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove SQLite from AXON entirely — fix the callsites that bypass the repository abstraction and hit `aiosqlite`/`sqlite3` directly, port the two standalone SQLite stores (`FailureStore`, `OutcomeStore`) to Postgres, delete the SQLite repositories + `SessionStore`'s SQLite connection + the SQL migrations + the `aiosqlite` dependency, so the only persistence backend left is Postgres.

**Architecture:** The relational concerns (decisions, ADRs, sessions, graph nodes/edges, file_index) already have Postgres implementations and the runtime defaults to them. What blocks "remove SQLite" is the long tail: (1) six callsites that reach `aiosqlite`/`sqlite3` directly, ignoring the backend switch; (2) two standalone SQLite stores (`FailureStore` → `failures.db`, `OutcomeStore` → `outcomes.db`) with no PG equivalent; (3) the SQLite repository classes + `SessionStore`'s SQLite connection + `store/migrations/*.sql` + the `aiosqlite` dependency themselves. **Per the owner's decision, historical session/commit data starts CLEAN in Postgres — there is NO SQLite→PG backfill of `sessions`/`session_memory`/`session_note`/`code_change`/`commits`.** `decisions`/`adr` were already backfilled (Phase-0 work); `nodes`/`edges`/`file_index` regenerate via re-index.

**Tech Stack:** Python 3.11+, `asyncpg` + Postgres, Typer (`pb` CLI), pytest + `testcontainers.postgres`. Removes `aiosqlite`. No new dependencies.

## Global Constraints

- NO data backfill of historical session/commit tables — Postgres starts empty for them (owner's "start clean" decision). Do not write a backfill; do not block on missing history.
- Every read/write must go through the repository abstraction (`SessionStore._decisions()/_sessions()/_graph()`, the file-cache factory) which already dispatches to Postgres. The six direct-SQLite callsites are the bugs to fix.
- `FailureStore` and `OutcomeStore` get Postgres equivalents (small tables) OR, if the `expansion` subsystem that owns them is confirmed unused, are dropped — Task 5 decides with a usage check, not a guess.
- After this phase: no `import aiosqlite` / `import sqlite3` anywhere in `src/` (the legacy-read in `decision_backfill.py` is the one allowed exception IF that one-shot migration tool is kept; otherwise it goes too). A guard test enforces this.
- Delete: `SqliteSessionRepository`, `SqliteDecisionRepository`, `SqliteGraphRepository`, `SqliteFileCache`, `sqlite_helpers.py`, `SessionStore`'s `aiosqlite` connection/lock/migration-runner, `store/migrations/*.sql`, and the SQLite-lock-specific pending-dir fallback (dec-112) where it only existed to survive SQLite write contention.
- Out of scope (already done): vector (Phase 1), Redis/dep-graph (Phase 2). The file-backed `TraceStore`/compression telemetry stay file-backed (dec-119, explicitly out of scope in dec-121).
- Validation prefixes with `rtk`. Export `AXON_PG_URL=postgresql://axon:axon@localhost:5434/axon`. `asyncio_mode="auto"`. Stage only each task's named files (never `git add -A`). **Lesson from Phases 1–2: every field/dep/module removal needs a REPO-WIDE grep including `scripts/`, not just the obvious files.**

---

### Task 1: Add the repository methods the bypass callsites need

Three of the six bypass callsites exist because the repository protocol is missing a method, so callers reached into `store._connection()` directly. Add those methods (with Postgres implementations) first, so Task 2 can repoint cleanly.

**Files:**
- Modify: `src/axon/store/pg_decision_repository.py` (+ the protocol/interface it implements) — add `latest_decision_ts()`, `validation_stats()`, `all_projects()`
- Modify: `src/axon/store/decision_repository.py` (the SQLite impl) — same methods, so both backends satisfy the protocol until the SQLite one is deleted in Task 6
- Test: `tests/store/test_decision_repo_methods.py`

**Interfaces:**
- Produces on the decision repository: `async latest_decision_ts() -> str | None` (max timestamp, replaces the raw query in `__main__.py:338`); `async validation_stats() -> dict` (the aggregate stats `validation/aggregate.py:30` computes via `json_extract`, expressed with PG JSONB operators); `async all_projects() -> list[str]` (`SELECT DISTINCT frontmatter->>'repo'`, replaces `pb.py adr sync:1534`).

- [ ] **Step 1: Read the three bypass callsites** to capture the exact SQL/semantics each needs: `src/axon/__main__.py` (~338-348, latest decision timestamp), `src/axon/validation/aggregate.py` (~30-50, the json_extract stats), `src/axon/cli/pb.py` (~1534-1536, `DISTINCT project FROM adr`). Reproduce their EXACT result shape.

- [ ] **Step 2: Write failing tests** (real container) for the three new methods against `PostgresDecisionRepository`, asserting the same shapes the callsites expect.

- [ ] **Step 3: Run red.**

- [ ] **Step 4: Implement** the three methods on both repositories (PG via JSONB operators `frontmatter->>'...'`; SQLite via the existing `json_extract` so the protocol stays satisfied until Task 6). Add them to the repository Protocol/ABC.

- [ ] **Step 5: Run green** — `rtk pytest tests/store/test_decision_repo_methods.py -q`.

- [ ] **Step 6: Lint + commit** — `feat(store): add latest_decision_ts/validation_stats/all_projects to the decision repository`.

---

### Task 2: Repoint the six SQLite-bypass callsites through the abstraction

**Files (one coherent change — each callsite stops touching SQLite directly):**
- Modify: `src/axon/__main__.py` (~338) → use `store.latest_decision_ts()`
- Modify: `src/axon/validation/aggregate.py` (~30) → use `store.validation_stats()`
- Modify: `src/axon/cli/pb.py` (~1534, the `adr sync` command) → use `store.all_projects()`
- Modify: `src/axon/pet/familiar.py` (~186) → route the ADR dashboard read through `SessionStore`/the decision repo instead of `sqlite3.connect(...mode=ro)`
- Modify: `src/axon/expansion/service.py` (~521, `_reindex_publish_path`) → use the file-cache factory (the `_open_file_cache()` pattern that honours `fileindex_backend`) instead of constructing `SqliteFileCache` directly
- Tests: extend each callsite's existing test; where a callsite had no test, add a focused one

- [ ] **Step 1: Enumerate + confirm** — `rtk proxy grep -rn "aiosqlite\|sqlite3\|_connection()\|SqliteFileCache(" src/ | grep -v "store/migrations\|sqlite_helpers\|decision_backfill"`. This is the authoritative list of remaining direct-SQLite usages outside the repo classes; every hit here must be repointed in this task (the 5 above) or is a repo-class internal (Task 6).

- [ ] **Step 2: Write/extend failing tests** — for each callsite, a test that exercises it against the Postgres backend (real container) and would fail while it still hits SQLite (e.g. familiar dashboard reads the backfilled ADRs from PG, not an empty `axon.db`).

- [ ] **Step 3: Repoint each callsite** through the abstraction (methods from Task 1 + the file-cache factory). Remove the now-unused `sqlite3`/`aiosqlite` imports from each file.

- [ ] **Step 4: Run green** — `rtk pytest tests/ -k "doctor or validation or adr_sync or familiar or expansion" -q` (with `AXON_PG_URL`), plus a repo-wide compile.

- [ ] **Step 5: Lint + commit** — `fix: route the 6 SQLite-bypass callsites through the Postgres repository abstraction`.

---

### Task 3: Postgres `FailureStore`

**Files:**
- Modify: `src/axon/store/failure_store.py` (back it with Postgres) OR create `src/axon/store/pg_failure_store.py` + a factory
- Modify: the `expansion` callers that construct `FailureStore`
- Test: `tests/store/test_pg_failure_store.py`

- [ ] **Step 1: Read** `failure_store.py` for its exact schema + method surface (it owns its own `aiosqlite` connection to `data/failures.db`). Mirror the surface on Postgres (one `failures` table).
- [ ] **Step 2: Write the failing test** (real container) covering each method.
- [ ] **Step 3: Run red.**
- [ ] **Step 4: Implement** the Postgres-backed store (asyncpg, `ensure_schema`, same method names); repoint the `expansion` constructors. Remove the `data/failures.db` path.
- [ ] **Step 5: Run green** — `rtk pytest tests/store/test_pg_failure_store.py tests/expansion -q`.
- [ ] **Step 6: Lint + commit** — `feat(store): Postgres-backed FailureStore (retire failures.db)`.

---

### Task 4: Postgres `OutcomeStore`

**Files:** Same shape as Task 3 for `src/axon/store/outcome_store.py` (`data/outcomes.db`).

- [ ] **Step 1: Read** `outcome_store.py` for its schema/methods.
- [ ] **Step 2–5:** failing test (real container) → red → Postgres implementation + repoint `expansion` callers (remove `data/outcomes.db`) → green (`rtk pytest tests/store/test_pg_outcome_store.py tests/expansion -q`).
- [ ] **Step 6: Lint + commit** — `feat(store): Postgres-backed OutcomeStore (retire outcomes.db)`.

> **Note (usage check before Tasks 3–4):** if a quick check shows the `expansion` subsystem (`src/axon/expansion/`) is not wired into any live MCP tool / CLI command, raise it — dropping `FailureStore`/`OutcomeStore` (and possibly the subsystem) may be simpler than porting. Decide with the human, do not assume.

---

### Task 5: Delete the SQLite repositories and `SessionStore`'s SQLite connection

**Files:**
- Modify: `src/axon/store/session_store.py` — remove the `aiosqlite` connection, the `asyncio.Lock`, the WAL PRAGMAs, the SQL-migration runner, and the `_graph()/_decisions()/_sessions()` SQLite branches (leave only the Postgres branches; the backend switch collapses to Postgres-only)
- Delete: `src/axon/store/session_repository.py` (`SqliteSessionRepository`), `src/axon/store/decision_repository.py` (`SqliteDecisionRepository`), `src/axon/store/graph_repository.py` (`SqliteGraphRepository`), `src/axon/store/sqlite_helpers.py`
- Modify: `src/axon/store/file_cache.py` — delete `SqliteFileCache` (keep the factory returning `PostgresFileCache` only)
- Delete: `src/axon/store/migrations/*.sql` (the SQLite migrations; keep `migrations/pg/`)
- Modify: `src/axon/config/runtime.py` — remove the `*_backend` switches that selected SQLite (or hard-pin them to `postgres`) and the `db_path` field if nothing else needs it
- Modify: any test importing the deleted SQLite classes

- [ ] **Step 1: Prove the SQLite repos are unreferenced** outside their own files/tests — `rtk proxy grep -rn "Sqlite\(Session\|Decision\|Graph\)Repository\|SqliteFileCache\|sqlite_helpers\|aiosqlite" src/ scripts/ tests/`. After Tasks 1–4 the only hits should be the classes themselves + the `decision_backfill` legacy reader. Repoint/remove each remaining importer.

- [ ] **Step 2: Write the guard test** `tests/test_no_sqlite.py` (mirror `tests/test_no_qdrant.py`): no `import aiosqlite` / `import sqlite3` in `src/` (allow `decision_backfill.py` ONLY if the one-shot migration tool is kept — otherwise no exceptions); `aiosqlite` absent from `pyproject.toml`; the deleted repo modules absent. Run it RED.

- [ ] **Step 3: Delete + collapse** — remove the SQLite repos, `sqlite_helpers`, the SQL migrations, and `SessionStore`'s SQLite plumbing; collapse the backend switches to Postgres-only.

- [ ] **Step 4: Verify** — `rtk python3 -m compileall src/axon` clean; `rtk pytest tests/store -q` (with `AXON_PG_URL`) green; the guard test green.

- [ ] **Step 5: Commit** — `feat(store): delete the SQLite repositories + SessionStore SQLite connection (Postgres-only)`.

---

### Task 6: Drop `aiosqlite`, the pending-dir SQLite fallback, and finalize the guard

**Files:**
- Modify: `pyproject.toml` (remove `aiosqlite`)
- Modify: `src/axon/store/pending.py` and the capture path — remove the SQLite-lock-specific pending-dir fallback (dec-112) IF it only existed to survive SQLite write contention; Postgres has no such lock-contention failure mode. Verify against dec-112's rationale before removing; keep the crash-durability pending dir if it serves a Postgres failure mode too.
- Modify: `decision_backfill.py` — decide: keep as a one-shot legacy reader (the only sanctioned `sqlite3` use) or delete now that the migration ran. If kept, the `test_no_sqlite` guard whitelists exactly this file.
- Modify: `docs/decisions/dec-121-postgres-unified-storage.md` (flip the overall ADR to `accepted` — all three slices done), `CLAUDE.md` D1/D4 (storage model is now Postgres-only), `docs/ADR.md` (mark dec-101 fully superseded)

- [ ] **Step 1: Repo-wide grep** — `rtk proxy grep -rn "aiosqlite\|sqlite" pyproject.toml src/ scripts/` — confirm only the sanctioned/whitelisted usages remain.
- [ ] **Step 2:** Remove `aiosqlite` from pyproject; apply the pending-dir decision; update the docs.
- [ ] **Step 3:** `rtk pytest tests/ -q` (with `AXON_PG_URL`) full green; `tests/test_no_sqlite.py` + `tests/test_no_qdrant.py` + `tests/test_no_redis.py` all green.
- [ ] **Step 4: Commit** — `feat: drop the aiosqlite dependency — AXON is Postgres-only (dec-121 complete)`.

---

### Task 7: Operational verification (Postgres-only end state)

**Files:** None (operational).

- [ ] **Step 1:** With a fresh shell (only `AXON_PG_URL` set, no SQLite file present), run `pb doctor`, a re-index, and the core MCP tools (`search_code`, `get_dependencies`, `get_adrs`, `recall_context`) — confirm they operate entirely against Postgres.
- [ ] **Step 2:** Confirm no `data/axon.db` / `data/failures.db` / `data/outcomes.db` is created on a clean run.
- [ ] **Step 3:** Confirm the per-repo decision/ADR counts (backfilled in Phase 0) and that re-index repopulated `nodes`/`edges`/`file_index`/`symbol_deps`/`embeddings`.
- [ ] **Step 4:** Update the AXON session memory / `dec-121` doc: the unification is complete — Postgres is the single backend; Qdrant, Redis, and SQLite are all retired.

---

## Self-Review

**Spec coverage:** six bypass callsites → Tasks 1–2; `FailureStore`/`OutcomeStore` → Tasks 3–4; delete SQLite repos + `SessionStore` connection + migrations → Task 5; drop `aiosqlite` + pending-dir fallback + docs → Task 6; operational → Task 7. NO historical backfill (owner's start-clean decision). `TraceStore`/telemetry stay file-backed (dec-119). Vector + Redis already retired (Phases 1–2).

**Known risks:** the `expansion` subsystem owns `FailureStore`/`OutcomeStore` — Tasks 3–4 include a usage check that may turn into "drop, don't port". The pending-dir fallback (dec-112) removal in Task 6 must be verified against its crash-durability rationale, not assumed. `decision_backfill.py`'s legacy `sqlite3` reader is the single sanctioned SQLite use; the guard whitelists it explicitly if kept.
