# session continuity -> Postgres + consolidation Plan (step 3, wave 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move session-continuity tables to Postgres behind a `SessionRepository` Protocol (SessionStore delegates), copy the data, flip the default, and consolidate the four per-concern backend flags into an `AXON_DB_BACKEND` master switch - closing dec-121 step 3.

**Architecture:** Mirror waves 2-3: extract `SqliteSessionRepository` (behavior-preserving), add `PostgresSessionRepository` (asyncpg, plain columns), SessionStore picks by `AXON_SESSIONS_BACKEND` and delegates. Then `_resolve_concern_backend` gives all concerns an `AXON_DB_BACKEND` fallback tier.

**Tech Stack:** Python 3.11+, asyncpg, PostgreSQL, testcontainers[postgres], pytest.

## Global Constraints

- Precedence EXACTLY: `AXON_SESSIONS_BACKEND` env > `axon.toml [runtime] sessions_backend` > default (until the consolidation task adds the `AXON_DB_BACKEND` tier).
- Constrained to `{"sqlite","postgres"}`; unknown raises `ValueError`.
- `sessions_backend` default stays `"sqlite"` until Task 6. Do NOT flip earlier.
- `RuntimeConfig.sessions_backend` is a DEFAULTED trailing field (`= "sqlite"`).
- The 9 session-method signatures and return shapes are UNCHANGED; consumers (pb.py, mcp/server.py) stay as-is.
- `PostgresSessionRepository` matches `SqliteSessionRepository`: memory/note insert returns the new id (`RETURNING id` vs `lastrowid`); code_change upsert on (commit_hash,file_path); session upsert on id; end_session SELECT-then-UPDATE returning repo/None; ordering DESC + LIMIT.
- The `save_code_change` db-locked pending fallback stays SQLite-only; the Postgres `save_code_change` == `save_code_change_inner`.
- `Models` SessionMemory/SessionNote/CodeChange import from `axon.store.session_store`. SessionStore's repo imports stay LAZY (inside the accessor).
- Only plain hyphens. No live backend in unit tests except the testcontainers conformance tests.

---

### Task 1: `sessions_backend` config + conftest pin

**Files:** Modify `src/axon/config/runtime.py`, `tests/conftest.py`; Test `tests/config/test_sessions_backend.py`.

- [ ] **Step 1: Write the failing test** (mirror `tests/config/test_decisions_backend.py`, replacing `DECISIONS`->`SESSIONS` and `decisions`->`sessions`; default `"sqlite"`).

- [ ] **Step 2: Run -> FAIL** (`.venv/Scripts/python.exe -m pytest tests/config/test_sessions_backend.py -v`).

- [ ] **Step 3: Implement** - add `sessions_backend: str = "sqlite"` trailing field; `_VALID_SESSIONS_BACKENDS` + `_resolve_sessions_backend(overrides)` (env `AXON_SESSIONS_BACKEND` > toml `sessions_backend` > `"sqlite"`, validated); wire into `load_runtime_config`; add `"sessions_backend"` to the toml allowlist. In `tests/conftest.py`, add `monkeypatch.setenv("AXON_SESSIONS_BACKEND", "sqlite")` next to the other three backend pins.

- [ ] **Step 4: Run -> 3 passed.**

- [ ] **Step 5: Commit** `feat(sessions): RuntimeConfig.sessions_backend (env > axon.toml > default sqlite) + conftest pin`

---

### Task 2: Extract SessionRepository Protocol + SqliteSessionRepository

**Files:** Create `src/axon/store/session_repository.py`; Modify `src/axon/store/session_store.py`; guard = existing session tests.

**Interfaces:** `SessionRepository(Protocol)` with `save_session_memory`, `get_session_memories`, `save_note`, `get_notes`, `save_code_change`, `save_code_change_inner`, `get_recent_changes`, `save_session`, `end_session`, plus the full-scan copy helpers `all_memories`, `all_notes`, `all_code_changes`, `all_sessions`. `SqliteSessionRepository(session)`.

- [ ] **Step 1: Create the Protocol + SqliteSessionRepository (move SQL verbatim)**

Create `src/axon/store/session_repository.py`: `SessionRepository(Protocol)` with the 9 method signatures (copy from `session_store.py`: `save_session_memory`/`get_session_memories` ~182-211, `save_note`/`get_notes` ~214-241, `_save_code_change_inner`/`save_code_change`/`get_recent_changes` ~244-305, `save_session`/`end_session` ~407-440), plus `all_code_changes`/`all_sessions`. Then `SqliteSessionRepository(session)` moving each body VERBATIM (self -> self._session); rename `_save_code_change_inner` -> `save_code_change_inner`; keep `save_code_change`'s db-locked fallback. Import `SessionMemory, SessionNote, CodeChange` and the pending helpers from `axon.store.session_store`. Add:

```python
    async def all_memories(self):
        import aiosqlite as _a
        from axon.store.session_store import SessionMemory
        from datetime import datetime
        async with self._session._lock:
            db = await self._session._connection()
            db.row_factory = _a.Row
            rows = await db.execute_fetchall("SELECT * FROM session_memory ORDER BY created_at")
        return [SessionMemory(id=r["id"], project=r["project"], summary=r["summary"],
                              raw_turns=r["raw_turns"], created_at=datetime.fromisoformat(r["created_at"]))
                for r in rows]

    async def all_notes(self):
        import aiosqlite as _a
        from axon.store.session_store import SessionNote
        from datetime import datetime
        async with self._session._lock:
            db = await self._session._connection()
            db.row_factory = _a.Row
            rows = await db.execute_fetchall("SELECT * FROM session_note ORDER BY created_at")
        return [SessionNote(id=r["id"], project=r["project"], body=r["body"],
                            created_at=datetime.fromisoformat(r["created_at"])) for r in rows]

    async def all_code_changes(self):
        import aiosqlite as _a
        from axon.store.session_store import CodeChange
        from datetime import datetime
        async with self._session._lock:
            db = await self._session._connection()
            db.row_factory = _a.Row
            rows = await db.execute_fetchall("SELECT * FROM code_change ORDER BY changed_at")
        return [CodeChange(commit_hash=r["commit_hash"], file_path=r["file_path"],
                           diff_summary=r["diff_summary"], why=r["why"],
                           changed_at=datetime.fromisoformat(r["changed_at"])) for r in rows]

    async def all_sessions(self):
        import aiosqlite as _a
        async with self._session._lock:
            db = await self._session._connection()
            db.row_factory = _a.Row
            rows = await db.execute_fetchall(
                "SELECT id, agent, repo, started_at, ended_at, context_payload FROM sessions ORDER BY started_at")
        return [dict(r) for r in rows]
```

- [ ] **Step 2: Make SessionStore delegate**

Add `self._session_repo = None` to `__init__`; add `_sessions()` lazy accessor (sqlite path only this task) returning `SqliteSessionRepository(self)`. Replace the 9 session-method bodies with `repo = await self._sessions(); return await repo.<method>(...)`. In `drain_pending`, change the `code_change` branch to `await (await self._sessions()).save_code_change_inner(CodeChange(...))`. Keep `_save_code_change_inner` as a thin delegator (`return await (await self._sessions()).save_code_change_inner(change)`) for any monkeypatch-based test.

- [ ] **Step 3: Run the existing session tests (behavior preserved)**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q -p no:cacheprovider -k "session or memory or note or code_change or drain" 2>&1 | tail -8`
Expected: green (pure refactor).

- [ ] **Step 4: Commit** `refactor(sessions): extract SqliteSessionRepository (+ all_* helpers); SessionStore delegates`

---

### Task 3: PostgresSessionRepository

**Files:** Create `src/axon/store/pg_session_repository.py`; Test `tests/store/test_pg_session_repository.py`.

- [ ] **Step 1: Write the failing test (testcontainers[postgres])**

```python
# tests/store/test_pg_session_repository.py
from __future__ import annotations

from datetime import UTC, datetime

import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer  # noqa: E402

from axon.store.session_store import CodeChange, SessionMemory, SessionNote  # noqa: E402


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


async def test_memory_note_return_ids_and_order(pg_dsn) -> None:
    from axon.store.pg_session_repository import PostgresSessionRepository

    repo = PostgresSessionRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        await repo.ensure_schema()  # idempotent
        i1 = await repo.save_session_memory(SessionMemory(project="p", summary="a", raw_turns=1))
        i2 = await repo.save_session_memory(SessionMemory(project="p", summary="b", raw_turns=2))
        assert isinstance(i1, int) and i2 > i1
        mems = await repo.get_session_memories("p", limit=3)
        assert [m.summary for m in mems][0] in {"a", "b"} and len(mems) == 2
        nid = await repo.save_note(SessionNote(project="p", body="n"))
        assert isinstance(nid, int) and nid >= 1
        assert len(await repo.get_notes("p")) == 1
    finally:
        await repo.close()


async def test_code_change_upsert_and_session_lifecycle(pg_dsn) -> None:
    from axon.store.pg_session_repository import PostgresSessionRepository

    repo = PostgresSessionRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        async with (await repo._ensure_pool()).acquire() as con:
            await con.execute("TRUNCATE code_change"); await con.execute("TRUNCATE sessions")
        cc = CodeChange(commit_hash="abc", file_path="f.py", diff_summary="d", why="w")
        await repo.save_code_change(cc)
        await repo.save_code_change(CodeChange(commit_hash="abc", file_path="f.py", diff_summary="d2", why="w2"))  # upsert
        recent = await repo.get_recent_changes("f.py")
        assert len(recent) == 1 and recent[0].diff_summary == "d2"
        await repo.save_session("s1", "manual", "axon", context_payload="ctx")
        assert await repo.end_session("s1") == "axon"
        assert await repo.end_session("missing") is None
    finally:
        await repo.close()
```

- [ ] **Step 2: Run -> FAIL** (module missing).

- [ ] **Step 3: Implement PostgresSessionRepository**

```python
# src/axon/store/pg_session_repository.py
"""Postgres-backed SessionRepository (dec-121 step 3, wave 4). Plain columns;
memory/note inserts use RETURNING id; code_change/session use ON CONFLICT;
no SQLite-lock fallback."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import asyncpg

from axon.store.session_store import CodeChange, SessionMemory, SessionNote


class PostgresSessionRepository:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
        return self._pool

    async def ensure_schema(self) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            await con.execute(
                "CREATE TABLE IF NOT EXISTS session_memory (id bigserial PRIMARY KEY,"
                " project text NOT NULL, summary text NOT NULL, raw_turns integer NOT NULL,"
                " created_at text NOT NULL)"
            )
            await con.execute(
                "CREATE TABLE IF NOT EXISTS session_note (id bigserial PRIMARY KEY,"
                " project text NOT NULL, body text NOT NULL, created_at text NOT NULL)"
            )
            await con.execute(
                "CREATE TABLE IF NOT EXISTS code_change (commit_hash text NOT NULL,"
                " file_path text NOT NULL, diff_summary text NOT NULL,"
                " why text NOT NULL DEFAULT '', changed_at text NOT NULL,"
                " PRIMARY KEY (commit_hash, file_path))"
            )
            await con.execute(
                "CREATE TABLE IF NOT EXISTS sessions (id text PRIMARY KEY, agent text NOT NULL,"
                " repo text NOT NULL, started_at text NOT NULL, ended_at text,"
                " context_payload text NOT NULL DEFAULT '{}')"
            )

    async def save_session_memory(self, mem: SessionMemory) -> int:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            return await con.fetchval(
                "INSERT INTO session_memory (project, summary, raw_turns, created_at)"
                " VALUES ($1, $2, $3, $4) RETURNING id",
                mem.project, mem.summary, mem.raw_turns, mem.created_at.isoformat(),
            )

    async def get_session_memories(self, project: str, limit: int = 3) -> list[SessionMemory]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT id, project, summary, raw_turns, created_at FROM session_memory"
                " WHERE project=$1 ORDER BY created_at DESC LIMIT $2",
                project, limit,
            )
        return [
            SessionMemory(id=r["id"], project=r["project"], summary=r["summary"],
                          raw_turns=r["raw_turns"], created_at=datetime.fromisoformat(r["created_at"]))
            for r in rows
        ]

    async def save_note(self, note: SessionNote) -> int:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            return await con.fetchval(
                "INSERT INTO session_note (project, body, created_at) VALUES ($1, $2, $3) RETURNING id",
                note.project, note.body, note.created_at.isoformat(),
            )

    async def get_notes(self, project: str, limit: int = 10) -> list[SessionNote]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT id, project, body, created_at FROM session_note"
                " WHERE project=$1 ORDER BY created_at DESC LIMIT $2",
                project, limit,
            )
        return [
            SessionNote(id=r["id"], project=r["project"], body=r["body"],
                        created_at=datetime.fromisoformat(r["created_at"]))
            for r in rows
        ]

    async def save_code_change_inner(self, change: CodeChange) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            await con.execute(
                "INSERT INTO code_change (commit_hash, file_path, diff_summary, why, changed_at)"
                " VALUES ($1, $2, $3, $4, $5)"
                " ON CONFLICT (commit_hash, file_path) DO UPDATE SET"
                " diff_summary=excluded.diff_summary, why=excluded.why, changed_at=excluded.changed_at",
                change.commit_hash, change.file_path, change.diff_summary, change.why,
                change.changed_at.isoformat(),
            )

    async def save_code_change(self, change: CodeChange) -> None:
        await self.save_code_change_inner(change)

    async def get_recent_changes(self, file_path: str, limit: int = 5) -> list[CodeChange]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT commit_hash, file_path, diff_summary, why, changed_at FROM code_change"
                " WHERE file_path=$1 ORDER BY changed_at DESC LIMIT $2",
                file_path, limit,
            )
        return [
            CodeChange(commit_hash=r["commit_hash"], file_path=r["file_path"],
                       diff_summary=r["diff_summary"], why=r["why"],
                       changed_at=datetime.fromisoformat(r["changed_at"]))
            for r in rows
        ]

    async def save_session(self, session_id, agent, repo, *, context_payload: str = "") -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            await con.execute(
                "INSERT INTO sessions (id, agent, repo, started_at, ended_at, context_payload)"
                " VALUES ($1, $2, $3, $4, NULL, $5)"
                " ON CONFLICT (id) DO UPDATE SET agent=excluded.agent, repo=excluded.repo,"
                " started_at=excluded.started_at, ended_at=excluded.ended_at,"
                " context_payload=excluded.context_payload",
                session_id, agent, repo, datetime.now(UTC).isoformat(),
                json.dumps({"recall": context_payload}),
            )

    async def end_session(self, session_id: str) -> str | None:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            repo = await con.fetchval("SELECT repo FROM sessions WHERE id=$1", session_id)
            if repo is not None:
                await con.execute(
                    "UPDATE sessions SET ended_at=$1 WHERE id=$2",
                    datetime.now(UTC).isoformat(), session_id,
                )
        return repo

    async def all_memories(self) -> list[SessionMemory]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT id, project, summary, raw_turns, created_at FROM session_memory ORDER BY created_at")
        return [
            SessionMemory(id=r["id"], project=r["project"], summary=r["summary"],
                          raw_turns=r["raw_turns"], created_at=datetime.fromisoformat(r["created_at"]))
            for r in rows
        ]

    async def all_notes(self) -> list[SessionNote]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT id, project, body, created_at FROM session_note ORDER BY created_at")
        return [
            SessionNote(id=r["id"], project=r["project"], body=r["body"],
                        created_at=datetime.fromisoformat(r["created_at"]))
            for r in rows
        ]

    async def all_code_changes(self) -> list[CodeChange]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT commit_hash, file_path, diff_summary, why, changed_at"
                " FROM code_change ORDER BY changed_at")
        return [
            CodeChange(commit_hash=r["commit_hash"], file_path=r["file_path"],
                       diff_summary=r["diff_summary"], why=r["why"],
                       changed_at=datetime.fromisoformat(r["changed_at"]))
            for r in rows
        ]

    async def all_sessions(self) -> list[dict]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT id, agent, repo, started_at, ended_at, context_payload"
                " FROM sessions ORDER BY started_at")
        return [dict(r) for r in rows]

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
```

- [ ] **Step 4: Run -> all pass.**

- [ ] **Step 5: Commit** `feat(sessions): PostgresSessionRepository (plain columns, RETURNING id, upsert, end_session)`

---

### Task 4: SessionStore selects the sessions backend

**Files:** Modify `src/axon/store/session_store.py`; Test `tests/store/test_session_sessions_backend.py`.

- [ ] **Step 1: Write the failing test** (mirror `tests/store/test_session_decisions_backend.py` with `_sessions()`, `PostgresSessionRepository`, `SqliteSessionRepository`, `AXON_SESSIONS_BACKEND`).

- [ ] **Step 2: Run -> FAIL.**

- [ ] **Step 3: Make `_sessions()` backend-aware** (mirror `_decisions()`): `load_runtime_config().sessions_backend == "postgres"` -> `PostgresSessionRepository(rt.pg_url)` + `ensure_schema`, else `SqliteSessionRepository(self)`; close in `SessionStore.close()`.

- [ ] **Step 4: Run -> 2 passed.**

- [ ] **Step 5: Commit** `feat(sessions): SessionStore._sessions() selects repository by sessions_backend`

---

### Task 5: data-copy script

**Files:** Create `scripts/migrate_sessions.py`; Test `tests/scripts/test_migrate_sessions.py`.

- [ ] **Step 1: Write the failing test** - a `_FakeRepo` exposing `all_memories`/`all_notes`/`all_code_changes`/`all_sessions` (returning small lists; `all_sessions` returns dicts with `id`/`agent`/`repo`) and recording `save_session_memory`/`save_note`/`save_code_change_inner`/`save_session`. Assert `copy_sessions(src, dst)` returns `{"memories":N,...}` with the right counts and that each `save_*` was called.

- [ ] **Step 2: Run -> FAIL.**

- [ ] **Step 3: Implement**

```python
# scripts/migrate_sessions.py
"""One-shot copy of session continuity (memories/notes/code_changes/sessions)
from SQLite to Postgres (idempotent)."""
from __future__ import annotations


async def copy_sessions(src_repo, dst_repo) -> dict:
    counts = {"memories": 0, "notes": 0, "code_changes": 0, "sessions": 0}
    for m in await src_repo.all_memories():
        await dst_repo.save_session_memory(m); counts["memories"] += 1
    for n in await src_repo.all_notes():
        await dst_repo.save_note(n); counts["notes"] += 1
    for c in await src_repo.all_code_changes():
        await dst_repo.save_code_change_inner(c); counts["code_changes"] += 1
    for s in await src_repo.all_sessions():
        await dst_repo.save_session(
            s["id"], s["agent"], s["repo"],
            context_payload="",  # session re-save preserves id; payload is advisory (see note)
        ); counts["sessions"] += 1
    return counts


async def _main() -> None:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from axon.config.runtime import load_runtime_config
    from axon.store.pg_session_repository import PostgresSessionRepository
    from axon.store.session_repository import SqliteSessionRepository
    from axon.store.session_store import SessionStore

    rt = load_runtime_config()
    session = SessionStore(db_path=rt.db_path)
    await session.init()
    src = SqliteSessionRepository(session)
    dst = PostgresSessionRepository(rt.pg_url)
    try:
        await dst.ensure_schema()
        counts = await copy_sessions(src, dst)
        print(f"copied {counts} -> postgres")
    finally:
        await session.close()
        await dst.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(_main())
```

NOTE: the `context_payload` re-wrap (`{"recall": ...}`) means a straight re-save double-wraps. For the copy, the plan's `save_session` re-wraps; since the live `sessions` data is tiny and the payload is advisory, this is acceptable - document it. If exact payload fidelity is required, add a `save_session_raw` that writes the row verbatim; the plan keeps the simple path given the data volume.

- [ ] **Step 4: Run -> passed.**

- [ ] **Step 5: Commit** `feat(sessions): one-shot SQLite->Postgres session-continuity copy script`

---

### Task 6: Cutover - copy, validate, flip (controller-run)

**Files:** Modify `src/axon/config/runtime.py`, `tests/config/test_sessions_backend.py`, `docs/MIGRATION.md`.

- [ ] **Step 1: Acceptance gate (operator-run)**

```bash
docker compose up -d axon-postgres
PYTHONPATH=src AXON_PG_URL="postgresql://axon:axon@localhost:5433/axon" .venv/Scripts/python.exe scripts/migrate_sessions.py
docker compose exec -T axon-postgres psql -U axon -d axon -tAc "SELECT (SELECT count(*) FROM session_note),(SELECT count(*) FROM sessions),(SELECT count(*) FROM code_change),(SELECT count(*) FROM session_memory);"
# parity: get_recent_changes / get_session_memories match under AXON_SESSIONS_BACKEND=postgres.
```
Proceed only if counts match. Else STOP.

- [ ] **Step 2: Update default test (RED)** - rename to `test_sessions_backend_defaults_to_postgres`, expect `"postgres"`.

- [ ] **Step 3: Run -> FAIL.**

- [ ] **Step 4: Flip** `_resolve_sessions_backend` fallback `"sqlite"` -> `"postgres"`.

- [ ] **Step 5: Run + sweep** `.venv/Scripts/python.exe -m pytest tests/config/test_sessions_backend.py tests/store tests/cli -q -p no:cacheprovider 2>&1 | tail -6`. The conftest autouse pin keeps SessionStore tests on sqlite; no per-file pins expected.

- [ ] **Step 6: Runbook + commit** - add the sessions wave note to `docs/MIGRATION.md`. Commit `feat(sessions): cutover - default sessions backend is now postgres (sqlite via override/rollback)`.

---

### Task 7: Consolidation - AXON_DB_BACKEND master switch

**Files:** Modify `src/axon/config/runtime.py`; Test `tests/config/test_db_backend.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/config/test_db_backend.py
from __future__ import annotations


def test_db_backend_flips_all_concerns(monkeypatch) -> None:
    for v in ("AXON_FILEINDEX_BACKEND", "AXON_GRAPH_BACKEND", "AXON_DECISIONS_BACKEND", "AXON_SESSIONS_BACKEND"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("AXON_DB_BACKEND", "sqlite")
    from axon.config.runtime import load_runtime_config

    rt = load_runtime_config()
    assert rt.fileindex_backend == "sqlite"
    assert rt.graph_backend == "sqlite"
    assert rt.decisions_backend == "sqlite"
    assert rt.sessions_backend == "sqlite"


def test_per_concern_overrides_db_backend(monkeypatch) -> None:
    monkeypatch.setenv("AXON_DB_BACKEND", "sqlite")
    monkeypatch.setenv("AXON_GRAPH_BACKEND", "postgres")
    from axon.config.runtime import load_runtime_config

    rt = load_runtime_config()
    assert rt.graph_backend == "postgres"  # per-concern wins
    assert rt.decisions_backend == "sqlite"  # falls back to AXON_DB_BACKEND
```

- [ ] **Step 2: Run -> FAIL** (no AXON_DB_BACKEND tier yet).

- [ ] **Step 3: Implement the shared resolver**

Add a helper and route all four resolvers through it:

```python
def _resolve_concern_backend(concern: str, overrides: dict) -> str:
    raw = (
        os.environ.get(f"AXON_{concern.upper()}_BACKEND")
        or os.environ.get("AXON_DB_BACKEND")
        or overrides.get(f"{concern}_backend")
        or overrides.get("db_backend")
        or "postgres"
    )
    backend = raw.strip().lower()
    if backend not in ("sqlite", "postgres"):
        raise ValueError(
            f"Invalid {concern}_backend {backend!r}; expected one of ['sqlite', 'postgres']"
        )
    return backend
```

Replace each `_resolve_<concern>_backend(overrides)` body with
`return _resolve_concern_backend("<concern>", overrides)` (concerns:
`fileindex`, `graph`, `decisions`, `sessions`). Add `"db_backend"` to the toml
allowlist. (All four already default postgres, so the shared `"postgres"`
fallback is behavior-preserving for the per-concern default tests, which delenv
their env and assert postgres.)

- [ ] **Step 4: Run -> 2 passed; also re-run the four per-concern config tests to confirm still green.**

Run: `.venv/Scripts/python.exe -m pytest tests/config/test_db_backend.py tests/config/test_fileindex_backend.py tests/config/test_graph_backend.py tests/config/test_decisions_backend.py tests/config/test_sessions_backend.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Commit** `feat(config): AXON_DB_BACKEND master switch consolidating the per-concern flags (closes dec-121 step 3)`

---

## Notes for the executor

- Tasks 1-5, 7 are autonomous; Task 6 Step 1 is operator-run.
- Task 2 is a PURE refactor - existing session tests are the guard.
- After Task 2, SessionStore has no direct session SQL; after Task 4 it routes by backend.
- The conftest autouse pin (Task 1) keeps the whole suite isolated; do NOT add per-file pins.
- Do NOT touch the graph/decisions/file_index repositories (done in prior waves) except via the shared resolver in Task 7.
- The default flip (Task 6) and the consolidation (Task 7) are the last changes; Task 7 must keep the four per-concern default-postgres tests green.
