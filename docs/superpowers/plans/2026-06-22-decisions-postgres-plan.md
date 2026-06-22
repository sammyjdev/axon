# decisions/ADRs -> Postgres Implementation Plan (step 3, wave 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the `decisions` and `adr` tables to Postgres behind a `DecisionRepository` Protocol; `SessionStore` delegates its decision/ADR methods by `AXON_DECISIONS_BACKEND`; data is copied over. Decisions store JSON as JSONB. Sessions/memories stay SQLite.

**Architecture:** Extract the current SessionStore decision/ADR SQL into `SqliteDecisionRepository` (behavior-preserving, + a new `all_decisions`), add `PostgresDecisionRepository` (asyncpg, JSONB + native operators, RETURNING id), have SessionStore pick the repository by config and delegate. A one-shot script copies decisions + ADRs.

**Tech Stack:** Python 3.11+, asyncpg (jsonb codec), PostgreSQL, testcontainers[postgres], pytest.

## Global Constraints

- Precedence EXACTLY: `AXON_DECISIONS_BACKEND` env > `axon.toml [runtime] decisions_backend` > default.
- Constrained to `{"sqlite", "postgres"}`; unknown raises `ValueError`.
- Default stays `"sqlite"` until Task 6. Do NOT flip earlier.
- `RuntimeConfig.decisions_backend` is a DEFAULTED trailing field (`= "sqlite"`).
- The decision/ADR method signatures and return shapes are UNCHANGED; the 23 consumer call sites stay as-is.
- `Decision.judged` and `validation_score` live INSIDE the `frontmatter` JSON and MUST round-trip as real values. Do NOT add a `judged` column or use `validation_score == 0.0` as a sentinel (CLAUDE.md / dec-109).
- `PostgresDecisionRepository` matches `SqliteDecisionRepository`: save_decision upsert by id, JSON queries (symbols-contains / git_hash / repo), next_decision_id from COUNT, ADR insert returning id, get_adrs order+limit.
- The `save_adr` db-locked pending fallback stays SQLite-only; the Postgres `save_adr` equals `save_adr_inner`.
- Sessions/memories/notes/code_changes stay on SessionStore's aiosqlite connection - do NOT touch them. `drain_pending` stays in SessionStore.
- `Decision` is imported from `axon.core.decision`; `ADR` from `axon.store.session_store`. SessionStore's imports of the repositories are LAZY (inside the accessor) to avoid circular imports.
- Only plain hyphens. No live backend in unit tests except the PostgresDecisionRepository conformance tests (testcontainers[postgres]).

---

### Task 1: `decisions_backend` config + resolver

**Files:** Modify `src/axon/config/runtime.py`; Test `tests/config/test_decisions_backend.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/config/test_decisions_backend.py
from __future__ import annotations

import pytest


def test_decisions_backend_defaults_to_sqlite(monkeypatch) -> None:
    monkeypatch.delenv("AXON_DECISIONS_BACKEND", raising=False)
    from axon.config.runtime import load_runtime_config

    assert load_runtime_config().decisions_backend == "sqlite"


def test_decisions_backend_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("AXON_DECISIONS_BACKEND", "postgres")
    from axon.config.runtime import load_runtime_config

    assert load_runtime_config().decisions_backend == "postgres"


def test_decisions_backend_rejects_unknown(monkeypatch) -> None:
    monkeypatch.setenv("AXON_DECISIONS_BACKEND", "dynamo")
    from axon.config.runtime import load_runtime_config

    with pytest.raises(ValueError):
        load_runtime_config()
```

- [ ] **Step 2: Run -> FAIL** (`.venv/Scripts/python.exe -m pytest tests/config/test_decisions_backend.py -v`).

- [ ] **Step 3: Implement (mirror `graph_backend`)**

Add `decisions_backend: str = "sqlite"` (defaulted trailing field). Add `_VALID_DECISIONS_BACKENDS = ("sqlite", "postgres")` and `_resolve_decisions_backend(overrides)` (env `AXON_DECISIONS_BACKEND` > `overrides.get("decisions_backend")` > `"sqlite"`, validated, else `ValueError`). Add `decisions_backend=_resolve_decisions_backend(overrides),` to the `RuntimeConfig(...)` construction and `"decisions_backend"` to the toml allowlist.

- [ ] **Step 4: Run -> 3 passed.**

- [ ] **Step 5: Commit** `feat(decisions): RuntimeConfig.decisions_backend (env > axon.toml > default sqlite), validated`

---

### Task 2: Extract DecisionRepository Protocol + SqliteDecisionRepository (+ all_decisions)

**Files:** Create `src/axon/store/decision_repository.py`; Modify `src/axon/store/session_store.py`; guard = existing decision/ADR tests.

**Interfaces:** `DecisionRepository(Protocol)` with: `save_decision`, `find_decisions_by_symbol`, `find_decision_by_git_hash`, `find_decisions_by_repo`, `next_decision_id`, `save_adr`, `save_adr_inner`, `get_adrs`, `all_decisions`. `SqliteDecisionRepository(session)`.

- [ ] **Step 1: Create the Protocol + SqliteDecisionRepository (move SQL verbatim)**

Create `src/axon/store/decision_repository.py`. Declare `DecisionRepository(Protocol)` with the 9 method signatures (copy from `session_store.py`: `save_decision`, `find_decisions_by_symbol`, `find_decision_by_git_hash`, `find_decisions_by_repo`, `next_decision_id` at ~404-510; `_save_adr_inner`->`save_adr_inner`, `save_adr`, `get_adrs` at ~143-209; plus the NEW `all_decisions`). Then `SqliteDecisionRepository`:

```python
class SqliteDecisionRepository:
    """The original SessionStore decision/ADR SQL, sharing the session's conn+lock."""

    def __init__(self, session) -> None:
        self._session = session
```

Move each method body VERBATIM from `SessionStore`, changing only `self._connection()` -> `self._session._connection()` and `self._lock` -> `self._session._lock`. Rename `_save_adr_inner` -> `save_adr_inner` (keep `save_adr` calling `self.save_adr_inner` + its db-locked pending fallback verbatim). Import `Decision` from `axon.core.decision`, `ADR` from `axon.store.session_store`, plus `aiosqlite`, `json`, `datetime`, and the pending helpers the fallback uses (`_pending_paths`, `write_pending`, `emit_capture_warning`, `_warnings_log`, `_is_db_locked`) - import them from `axon.store.session_store`. Add the NEW method:

```python
    async def all_decisions(self):
        from axon.core.decision import Decision
        import json
        async with self._session._lock:
            db = await self._session._connection()
            import aiosqlite as _a
            db.row_factory = _a.Row
            rows = await db.execute_fetchall(
                "SELECT frontmatter FROM decisions ORDER BY created_at"
            )
        return [Decision(**json.loads(r["frontmatter"])) for r in rows]
```

- [ ] **Step 2: Make SessionStore delegate**

In `session_store.py`: add `self._decision_repo = None` to `__init__`; add a lazy accessor (sqlite path only this task):

```python
    async def _decisions(self):
        if self._decision_repo is None:
            from axon.store.decision_repository import SqliteDecisionRepository

            self._decision_repo = SqliteDecisionRepository(self)
        return self._decision_repo
```

Replace the bodies of `save_decision`, `find_decisions_by_symbol`, `find_decision_by_git_hash`, `find_decisions_by_repo`, `next_decision_id`, `save_adr`, `get_adrs` with `repo = await self._decisions(); return await repo.<method>(...)` (identical signatures). In `drain_pending`, change the adr branch from `await self._save_adr_inner(ADR(...))` to `await (await self._decisions()).save_adr_inner(ADR(...))`. Keep `_save_adr_inner` as a thin delegator too (some code/tests may call it): `async def _save_adr_inner(self, adr): return await (await self._decisions()).save_adr_inner(adr)`.

- [ ] **Step 3: Run the existing decision/ADR tests (behavior preserved)**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q -p no:cacheprovider -k "decision or adr or save_adr or drain or session_store" 2>&1 | tail -8`
Expected: green (pure refactor). Fix the delegation, not the tests.

- [ ] **Step 4: Commit** `refactor(decisions): extract SqliteDecisionRepository (+ all_decisions); SessionStore delegates`

---

### Task 3: PostgresDecisionRepository

**Files:** Create `src/axon/store/pg_decision_repository.py`; Test `tests/store/test_pg_decision_repository.py`.

- [ ] **Step 1: Write the failing test (testcontainers[postgres])**

```python
# tests/store/test_pg_decision_repository.py
from __future__ import annotations

from datetime import UTC, datetime

import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer  # noqa: E402

from axon.core.decision import Decision  # noqa: E402
from axon.store.session_store import ADR  # noqa: E402


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


def _dec(did, repo="axon", symbols=("sym1",), git_hash=None, judged=False, score=0.0):
    return Decision(
        id=did, timestamp=datetime(2026, 1, 1, tzinfo=UTC), agent="manual", repo=repo,
        symbols=list(symbols), summary="s", git_hash=git_hash, judged=judged,
        validation_score=score,
    )


async def test_decision_upsert_and_json_queries(pg_dsn) -> None:
    from axon.store.pg_decision_repository import PostgresDecisionRepository

    repo = PostgresDecisionRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        await repo.ensure_schema()  # idempotent
        await repo.save_decision(_dec("dec-001", symbols=["alpha"], git_hash="abc", judged=True, score=3.5))
        await repo.save_decision(_dec("dec-001", symbols=["alpha", "beta"]))  # upsert same id
        await repo.save_decision(_dec("dec-002", repo="other", symbols=["gamma"]))
        by_sym = await repo.find_decisions_by_symbol("beta")
        assert [d.id for d in by_sym] == ["dec-001"]
        by_repo = await repo.find_decisions_by_repo("axon")
        assert [d.id for d in by_repo] == ["dec-001"]
        assert await repo.next_decision_id() == "dec-003"  # COUNT=2 -> dec-003
        all_d = await repo.all_decisions()
        assert {d.id for d in all_d} == {"dec-001", "dec-002"}
    finally:
        await repo.close()


async def test_judged_roundtrip_and_git_hash(pg_dsn) -> None:
    from axon.store.pg_decision_repository import PostgresDecisionRepository

    repo = PostgresDecisionRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        async with (await repo._ensure_pool()).acquire() as con:
            await con.execute("TRUNCATE decisions")
        await repo.save_decision(_dec("dec-010", git_hash="deadbeef", judged=True, score=4.0))
        found = await repo.find_decision_by_git_hash("deadbeef", repo="axon")
        assert found is not None and found.judged is True and found.validation_score == 4.0
        assert await repo.find_decision_by_git_hash("deadbeef", repo="nope") is None
    finally:
        await repo.close()


async def test_adr_insert_returns_id_and_get(pg_dsn) -> None:
    from axon.store.pg_decision_repository import PostgresDecisionRepository

    repo = PostgresDecisionRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        async with (await repo._ensure_pool()).acquire() as con:
            await con.execute("TRUNCATE adr")
        adr = ADR(project="p", title="t", context="c", decision="d", rationale="r",
                  created_at=datetime(2026, 1, 1, tzinfo=UTC))
        new_id = await repo.save_adr(adr)
        assert isinstance(new_id, int) and new_id >= 1
        got = await repo.get_adrs("p")
        assert len(got) == 1 and got[0].title == "t"
    finally:
        await repo.close()
```

- [ ] **Step 2: Run -> FAIL** (module missing).

- [ ] **Step 3: Implement PostgresDecisionRepository**

```python
# src/axon/store/pg_decision_repository.py
"""Postgres-backed DecisionRepository (dec-121 step 3, wave 3).

decisions.frontmatter is JSONB (GIN-indexed); find_* use native operators.
ADR insert uses RETURNING id (Postgres has no lastrowid). judged/validation_score
live in frontmatter and round-trip as real JSON values. No SQLite-lock fallback.
"""
from __future__ import annotations

import json
from datetime import datetime

import asyncpg

from axon.core.decision import Decision
from axon.store.session_store import ADR


async def _init_conn(conn: asyncpg.Connection) -> None:
    # Return jsonb as Python dicts (and accept dicts on write).
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


class PostgresDecisionRepository:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._dsn, init=_init_conn, min_size=1, max_size=5
            )
        return self._pool

    async def ensure_schema(self) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            await con.execute(
                """
                CREATE TABLE IF NOT EXISTS decisions (
                    id          text PRIMARY KEY,
                    frontmatter jsonb NOT NULL,
                    body        text,
                    vault_path  text,
                    created_at  text NOT NULL
                )
                """
            )
            await con.execute(
                "CREATE INDEX IF NOT EXISTS ix_decisions_fm ON decisions USING gin (frontmatter)"
            )
            await con.execute(
                """
                CREATE TABLE IF NOT EXISTS adr (
                    id         bigserial PRIMARY KEY,
                    project    text NOT NULL,
                    title      text NOT NULL,
                    context    text NOT NULL,
                    decision   text NOT NULL,
                    rationale  text NOT NULL,
                    created_at text NOT NULL
                )
                """
            )

    async def save_decision(self, decision: Decision) -> None:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            await con.execute(
                """
                INSERT INTO decisions (id, frontmatter, body, vault_path, created_at)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (id) DO UPDATE SET
                    frontmatter=excluded.frontmatter, body=excluded.body,
                    vault_path=excluded.vault_path, created_at=excluded.created_at
                """,
                decision.id, decision.model_dump(mode="json"), decision.summary,
                None, decision.timestamp.isoformat(),
            )

    async def find_decisions_by_symbol(self, symbol_id: str) -> list[Decision]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT frontmatter FROM decisions"
                " WHERE EXISTS (SELECT 1 FROM jsonb_array_elements_text(frontmatter->'symbols') v"
                "               WHERE v = $1)"
                " ORDER BY created_at DESC",
                symbol_id,
            )
        return [Decision(**r["frontmatter"]) for r in rows]

    async def find_decision_by_git_hash(
        self, git_hash: str, *, repo: str | None = None
    ) -> Decision | None:
        pool = await self._ensure_pool()
        sql = "SELECT frontmatter FROM decisions WHERE frontmatter->>'git_hash' = $1"
        params: list = [git_hash]
        if repo is not None:
            params.append(repo)
            sql += " AND frontmatter->>'repo' = $2"
        sql += " ORDER BY created_at DESC LIMIT 1"
        async with pool.acquire() as con:
            rows = await con.fetch(sql, *params)
        if not rows:
            return None
        return Decision(**rows[0]["frontmatter"])

    async def find_decisions_by_repo(self, repo: str, limit: int = 20) -> list[Decision]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT frontmatter FROM decisions WHERE frontmatter->>'repo' = $1"
                " ORDER BY created_at DESC LIMIT $2",
                repo, limit,
            )
        return [Decision(**r["frontmatter"]) for r in rows]

    async def all_decisions(self) -> list[Decision]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch("SELECT frontmatter FROM decisions ORDER BY created_at")
        return [Decision(**r["frontmatter"]) for r in rows]

    async def next_decision_id(self) -> str:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            count = await con.fetchval("SELECT count(*) FROM decisions")
        return f"dec-{(count or 0) + 1:03d}"

    async def save_adr_inner(self, adr: ADR) -> int:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            return await con.fetchval(
                "INSERT INTO adr (project, title, context, decision, rationale, created_at)"
                " VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
                adr.project, adr.title, adr.context, adr.decision, adr.rationale,
                adr.created_at.isoformat(),
            )

    async def save_adr(self, adr: ADR) -> int:
        # No SQLite-lock fallback on Postgres - a direct insert.
        return await self.save_adr_inner(adr)

    async def get_adrs(self, project: str, limit: int = 10) -> list[ADR]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT id, project, title, context, decision, rationale, created_at"
                " FROM adr WHERE project=$1 ORDER BY created_at DESC LIMIT $2",
                project, limit,
            )
        return [
            ADR(
                id=r["id"], project=r["project"], title=r["title"], context=r["context"],
                decision=r["decision"], rationale=r["rationale"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
```

- [ ] **Step 4: Run -> all pass.**

- [ ] **Step 5: Commit** `feat(decisions): PostgresDecisionRepository (JSONB + GIN, native JSON queries, RETURNING id, judged round-trip)`

---

### Task 4: SessionStore selects the decisions backend

**Files:** Modify `src/axon/store/session_store.py`; Test `tests/store/test_session_decisions_backend.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/store/test_session_decisions_backend.py
from __future__ import annotations


async def test_session_decisions_routes_to_postgres(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AXON_DECISIONS_BACKEND", "postgres")
    constructed = {}

    class FakePgRepo:
        def __init__(self, dsn: str) -> None:
            constructed["dsn"] = dsn

        async def ensure_schema(self) -> None:
            constructed["ensured"] = True

    monkeypatch.setattr("axon.store.pg_decision_repository.PostgresDecisionRepository", FakePgRepo)

    from axon.store.session_store import SessionStore

    store = SessionStore(db_path=tmp_path / "axon.db")
    await store.init()
    repo = await store._decisions()
    assert isinstance(repo, FakePgRepo)
    assert constructed["ensured"] is True
    await store.close()


async def test_session_decisions_routes_to_sqlite(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AXON_DECISIONS_BACKEND", "sqlite")  # pinned, survives the flip
    from axon.store.decision_repository import SqliteDecisionRepository
    from axon.store.session_store import SessionStore

    store = SessionStore(db_path=tmp_path / "axon.db")
    await store.init()
    repo = await store._decisions()
    assert isinstance(repo, SqliteDecisionRepository)
    await store.close()
```

- [ ] **Step 2: Run -> FAIL** (postgres test - `_decisions()` always sqlite).

- [ ] **Step 3: Make `_decisions()` backend-aware**

```python
    async def _decisions(self):
        if self._decision_repo is None:
            from axon.config.runtime import load_runtime_config

            rt = load_runtime_config()
            if rt.decisions_backend == "postgres":
                from axon.store.pg_decision_repository import PostgresDecisionRepository

                self._decision_repo = PostgresDecisionRepository(rt.pg_url)
                await self._decision_repo.ensure_schema()
            else:
                from axon.store.decision_repository import SqliteDecisionRepository

                self._decision_repo = SqliteDecisionRepository(self)
        return self._decision_repo
```

Also close it in `SessionStore.close()`:

```python
        if self._decision_repo is not None and hasattr(self._decision_repo, "close"):
            await self._decision_repo.close()
```

- [ ] **Step 4: Run -> 2 passed.**

- [ ] **Step 5: Commit** `feat(decisions): SessionStore._decisions() selects repository by decisions_backend`

---

### Task 5: data-copy script

**Files:** Create `scripts/migrate_decisions.py`; Test `tests/scripts/test_migrate_decisions.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/scripts/test_migrate_decisions.py
from __future__ import annotations


class _FakeRepo:
    def __init__(self, decisions=None, adrs=None):
        self._decisions = decisions or []
        self._adrs = adrs or []
        self.saved_decisions = []
        self.saved_adrs = []

    async def all_decisions(self):
        return self._decisions

    async def get_adrs(self, project, limit=10):
        return [a for a in self._adrs if a.project == project][:limit]

    async def save_decision(self, d):
        self.saved_decisions.append(d.id)

    async def save_adr_inner(self, a):
        self.saved_adrs.append(a.title)
        return len(self.saved_adrs)


async def test_copy_decisions_counts() -> None:
    from datetime import UTC, datetime

    from axon.core.decision import Decision
    from scripts.migrate_decisions import copy_decisions

    d = Decision(id="dec-001", timestamp=datetime(2026, 1, 1, tzinfo=UTC), agent="manual",
                 repo="axon", summary="s")
    src = _FakeRepo(decisions=[d], adrs=[])
    dst = _FakeRepo()
    n_dec, n_adr = await copy_decisions(src, dst, adr_projects=[])
    assert (n_dec, n_adr) == (1, 0)
    assert dst.saved_decisions == ["dec-001"]
```

- [ ] **Step 2: Run -> FAIL.**

- [ ] **Step 3: Implement**

```python
# scripts/migrate_decisions.py
"""One-shot copy of decisions + ADRs from SQLite to Postgres (idempotent)."""
from __future__ import annotations


async def copy_decisions(src_repo, dst_repo, *, adr_projects) -> tuple[int, int]:
    decisions = await src_repo.all_decisions()
    for d in decisions:
        await dst_repo.save_decision(d)
    n_adr = 0
    for project in adr_projects:
        for adr in await src_repo.get_adrs(project, limit=10_000):
            await dst_repo.save_adr_inner(adr)
            n_adr += 1
    return len(decisions), n_adr


async def _main() -> None:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from axon.config.runtime import load_runtime_config
    from axon.store.decision_repository import SqliteDecisionRepository
    from axon.store.pg_decision_repository import PostgresDecisionRepository
    from axon.store.session_store import SessionStore

    rt = load_runtime_config()
    session = SessionStore(db_path=rt.db_path)
    await session.init()
    src = SqliteDecisionRepository(session)
    dst = PostgresDecisionRepository(rt.pg_url)
    try:
        await dst.ensure_schema()
        # derive the set of ADR projects from the decisions' repos
        projects = sorted({d.repo for d in await src.all_decisions()})
        n_dec, n_adr = await copy_decisions(src, dst, adr_projects=projects)
        print(f"copied {n_dec} decisions, {n_adr} adrs -> postgres")
    finally:
        await session.close()
        await dst.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(_main())
```

- [ ] **Step 4: Run -> 1 passed.**

- [ ] **Step 5: Commit** `feat(decisions): one-shot SQLite->Postgres decisions/ADR copy script`

---

### Task 6: Cutover - copy, validate, flip the default (controller-run)

**Files:** Modify `src/axon/config/runtime.py`; `tests/config/test_decisions_backend.py`; `docs/MIGRATION.md`.

- [ ] **Step 1: Acceptance gate (operator-run)**

```bash
docker compose up -d axon-postgres
PYTHONPATH=src AXON_PG_URL="postgresql://axon:axon@localhost:5433/axon" .venv/Scripts/python.exe scripts/migrate_decisions.py
docker compose exec -T axon-postgres psql -U axon -d axon -tAc "SELECT (SELECT count(*) FROM decisions), (SELECT count(*) FROM adr);"
# parity: find_decisions_by_repo + a judged round-trip match the sqlite result under AXON_DECISIONS_BACKEND=postgres.
```
Proceed only if counts match and a spot-checked decision's `judged` survived. Else STOP.

- [ ] **Step 2: Update default test (RED)** - rename to `test_decisions_backend_defaults_to_postgres`, expect `"postgres"`.

- [ ] **Step 3: Run -> FAIL.**

- [ ] **Step 4: Flip** `_resolve_decisions_backend` fallback `"sqlite"` -> `"postgres"`.

- [ ] **Step 5: Run + sweep** `.venv/Scripts/python.exe -m pytest tests/config/test_decisions_backend.py tests/store tests/cli -q -p no:cacheprovider 2>&1 | tail -6`. Pin any default-sqlite test that now hits postgres to `AXON_DECISIONS_BACKEND=sqlite` (same pattern as the graph fixtures).

- [ ] **Step 6: Runbook + commit** - add the decisions wave note to `docs/MIGRATION.md` (copy via `scripts/migrate_decisions.py`; JSONB; flip; rollback to sqlite intact). Commit `feat(decisions): cutover - default decisions backend is now postgres (sqlite via override/rollback)`.

---

## Notes for the executor

- Tasks 1-5 autonomous; Task 6 Step 1 operator-run.
- Task 2 is a PURE refactor - the existing decision/ADR tests are the guard.
- Watch the asyncpg jsonb codec: `model_dump(mode="json")` is a dict; with the codec asyncpg encodes it to jsonb and decodes back to a dict, so `Decision(**row["frontmatter"])` gets a dict. If the codec is NOT applied, asyncpg returns a JSON string and `Decision(**str)` fails - confirm the codec works in Task 3.
- Do NOT touch sessions/memories/notes/code_changes; `drain_pending` stays in SessionStore (only its ADR write delegates).
- The default flip is the LAST change.
