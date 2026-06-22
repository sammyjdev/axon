# file_index -> Postgres Implementation Plan (step 3, wave 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the file_index cache from SQLite to Postgres behind the existing `FileCache` Protocol, selectable by `AXON_FILEINDEX_BACKEND`, validated end-to-end, with SQLite staying default until the gated flip.

**Architecture:** A new `PostgresFileCache` implements the 4-method `FileCache` Protocol over asyncpg with its own pool and idempotent schema. `_open_file_cache()` selects the backend from `RuntimeConfig.fileindex_backend`. file_index is a cache, so no data migration - the flip re-builds it on the next index. Graph/decisions/sessions stay on SQLite (separate connection).

**Tech Stack:** Python 3.11+, asyncpg, PostgreSQL, testcontainers[postgres], frozen `@dataclass` RuntimeConfig, pytest.

## Global Constraints

- Backend precedence is EXACTLY: `AXON_FILEINDEX_BACKEND` env (set, non-empty) > `axon.toml [runtime] fileindex_backend` > default.
- `fileindex_backend` is constrained to `{"sqlite", "postgres"}`; unknown raises `ValueError` at config load.
- The default stays `"sqlite"` until Task 4 (the gated cutover) flips it to `"postgres"`. Do NOT flip earlier.
- `RuntimeConfig.fileindex_backend` is a DEFAULTED trailing field (`= "sqlite"`) so existing manual `RuntimeConfig(...)` constructions do not break.
- `PostgresFileCache` MUST match `SqliteFileCache` behavior byte-for-byte: the `status='done'` filter in `get_all_sha1s` (pending rows excluded), posix path normalization (`Path(file_path).as_posix()`), the `ON CONFLICT (file_path, ctx) DO UPDATE` upsert, and `list_entries` returning ALL statuses.
- Only plain hyphens `-`, never em or en dashes.
- No live backend in unit tests except the PostgresFileCache conformance tests, which use testcontainers[postgres].

---

### Task 1: `fileindex_backend` config + resolver

**Files:**
- Modify: `src/axon/config/runtime.py`
- Test: `tests/config/test_fileindex_backend.py`

**Interfaces:**
- Produces: `RuntimeConfig.fileindex_backend: str`; `_resolve_fileindex_backend(overrides: dict) -> str` (env > toml > default sqlite, validated).

- [ ] **Step 1: Write the failing test**

```python
# tests/config/test_fileindex_backend.py
from __future__ import annotations

import pytest


def test_fileindex_backend_defaults_to_sqlite(monkeypatch) -> None:
    monkeypatch.delenv("AXON_FILEINDEX_BACKEND", raising=False)
    from axon.config.runtime import load_runtime_config

    assert load_runtime_config().fileindex_backend == "sqlite"


def test_fileindex_backend_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("AXON_FILEINDEX_BACKEND", "postgres")
    from axon.config.runtime import load_runtime_config

    assert load_runtime_config().fileindex_backend == "postgres"


def test_fileindex_backend_rejects_unknown(monkeypatch) -> None:
    monkeypatch.setenv("AXON_FILEINDEX_BACKEND", "mongodb")
    from axon.config.runtime import load_runtime_config

    with pytest.raises(ValueError):
        load_runtime_config()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/config/test_fileindex_backend.py -v`
Expected: FAIL (`RuntimeConfig` has no attribute `fileindex_backend`).

- [ ] **Step 3: Add the field, resolver, wiring, and toml allowlist**

In `src/axon/config/runtime.py`, mirror the existing `vector_backend` / `_resolve_vector_backend` work:

Add a defaulted trailing field on `RuntimeConfig` (next to `vector_backend = "qdrant"` and `active_profile = None`):

```python
    fileindex_backend: str = "sqlite"
```

Add the resolver (next to `_resolve_vector_backend`):

```python
_VALID_FILEINDEX_BACKENDS = ("sqlite", "postgres")


def _resolve_fileindex_backend(overrides: dict) -> str:
    """Select the file_index backend: AXON_FILEINDEX_BACKEND env > axon.toml > default."""
    raw = (
        os.environ.get("AXON_FILEINDEX_BACKEND")
        or overrides.get("fileindex_backend")
        or "sqlite"
    )
    backend = raw.strip().lower()
    if backend not in _VALID_FILEINDEX_BACKENDS:
        raise ValueError(
            f"Invalid fileindex_backend {backend!r}; expected one of {list(_VALID_FILEINDEX_BACKENDS)}"
        )
    return backend
```

In `load_runtime_config()`, add the kwarg to the `RuntimeConfig(...)` construction (alongside `vector_backend=`):

```python
        fileindex_backend=_resolve_fileindex_backend(overrides),
```

Finally, add `"fileindex_backend"` to the axon.toml `[runtime]` allowed-keys allowlist (the same whitelist `vector_backend` was added to - find where `_load_toml_runtime_overrides` filters keys and add it there).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/config/test_fileindex_backend.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/axon/config/runtime.py tests/config/test_fileindex_backend.py
git commit -m "feat(fileindex): RuntimeConfig.fileindex_backend (env > axon.toml > default sqlite), validated"
```

---

### Task 2: PostgresFileCache

**Files:**
- Create: `src/axon/store/pg_file_cache.py`
- Test: `tests/store/test_pg_file_cache.py`

**Interfaces:**
- Consumes: `asyncpg`.
- Produces: `PostgresFileCache(dsn)` implementing the `FileCache` Protocol (`get_all_sha1s`, `set_entry`, `delete_entry`, `list_entries`) + `ensure_schema()` + `close()`.

- [ ] **Step 1: Write the failing test (testcontainers[postgres])**

```python
# tests/store/test_pg_file_cache.py
from __future__ import annotations

import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer  # noqa: E402


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


async def test_set_then_get_excludes_pending(pg_dsn) -> None:
    from axon.store.pg_file_cache import PostgresFileCache

    cache = PostgresFileCache(dsn=pg_dsn)
    try:
        await cache.ensure_schema()
        await cache.ensure_schema()  # idempotent
        await cache.set_entry("a/b.py", "knowledge", "sha-done", 3)
        await cache.set_entry("a/c.py", "knowledge", "sha-pending", 0, status="pending")
        done = await cache.get_all_sha1s("knowledge")
        assert done == {"a/b.py": "sha-done"}  # pending excluded
        all_rows = dict(await cache.list_entries("knowledge"))
        assert set(all_rows) == {"a/b.py", "a/c.py"}  # list_entries shows all statuses
    finally:
        await cache.close()


async def test_set_is_idempotent_and_posix_normalized(pg_dsn) -> None:
    from axon.store.pg_file_cache import PostgresFileCache

    cache = PostgresFileCache(dsn=pg_dsn)
    try:
        await cache.ensure_schema()
        async with (await cache._ensure_pool()).acquire() as con:
            await con.execute("TRUNCATE file_index")
        # backslash and posix forms must collide on the same row
        await cache.set_entry("d\\e.py", "work", "sha-1", 1)
        await cache.set_entry("d/e.py", "work", "sha-2", 2)
        rows = await cache.list_entries("work")
        assert rows == [("d/e.py", "sha-2")]  # one row, updated in place
    finally:
        await cache.close()


async def test_delete_entry_removes_only_that_row(pg_dsn) -> None:
    from axon.store.pg_file_cache import PostgresFileCache

    cache = PostgresFileCache(dsn=pg_dsn)
    try:
        await cache.ensure_schema()
        async with (await cache._ensure_pool()).acquire() as con:
            await con.execute("TRUNCATE file_index")
        await cache.set_entry("x.py", "knowledge", "sx", 1)
        await cache.set_entry("y.py", "knowledge", "sy", 1)
        await cache.delete_entry("x.py", "knowledge")
        remaining = await cache.get_all_sha1s("knowledge")
        assert remaining == {"y.py": "sy"}
    finally:
        await cache.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/store/test_pg_file_cache.py -v`
Expected: FAIL (module `pg_file_cache` does not exist).

- [ ] **Step 3: Implement PostgresFileCache**

```python
# src/axon/store/pg_file_cache.py
"""Postgres-backed FileCache (dec-121 step 3, wave 1).

Implements the same FileCache Protocol surface as SqliteFileCache, byte-for-byte:
status='done' filter in get_all_sha1s, posix path normalization, ON CONFLICT
upsert, list_entries returning all statuses. Own asyncpg pool; no shared lock.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import asyncpg


class PostgresFileCache:
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
                """
                CREATE TABLE IF NOT EXISTS file_index (
                    file_path   text    NOT NULL,
                    ctx         text    NOT NULL,
                    sha1        text    NOT NULL,
                    status      text    NOT NULL DEFAULT 'done',
                    chunk_count integer NOT NULL DEFAULT 0,
                    indexed_at  text    NOT NULL,
                    PRIMARY KEY (file_path, ctx)
                )
                """
            )
            await con.execute(
                "CREATE INDEX IF NOT EXISTS ix_file_index_ctx ON file_index (ctx)"
            )
            await con.execute(
                "CREATE INDEX IF NOT EXISTS ix_file_index_status ON file_index (status)"
            )

    async def get_all_sha1s(self, ctx: str) -> dict[str, str]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT file_path, sha1 FROM file_index WHERE ctx=$1 AND status='done'",
                ctx,
            )
        return {r["file_path"]: r["sha1"] for r in rows}

    async def set_entry(
        self,
        file_path: str,
        ctx: str,
        sha1: str,
        chunk_count: int,
        *,
        status: str = "done",
    ) -> None:
        fp = Path(file_path).as_posix()
        now = datetime.now(UTC).isoformat()
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            await con.execute(
                """
                INSERT INTO file_index
                    (file_path, ctx, sha1, status, chunk_count, indexed_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (file_path, ctx) DO UPDATE SET
                    sha1        = excluded.sha1,
                    status      = excluded.status,
                    chunk_count = excluded.chunk_count,
                    indexed_at  = excluded.indexed_at
                """,
                fp, ctx, sha1, status, chunk_count, now,
            )

    async def delete_entry(self, file_path: str, ctx: str) -> None:
        fp = Path(file_path).as_posix()
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            await con.execute(
                "DELETE FROM file_index WHERE file_path=$1 AND ctx=$2", fp, ctx
            )

    async def list_entries(self, ctx: str) -> list[tuple[str, str]]:
        pool = await self._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT file_path, sha1 FROM file_index WHERE ctx=$1", ctx
            )
        return [(r["file_path"], r["sha1"]) for r in rows]

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/store/test_pg_file_cache.py -v`
Expected: all pass (round-trip, pending excluded, idempotent + posix collide, delete).

- [ ] **Step 5: Commit**

```bash
git add src/axon/store/pg_file_cache.py tests/store/test_pg_file_cache.py
git commit -m "feat(fileindex): PostgresFileCache (FileCache Protocol over asyncpg, status sentinel + posix parity)"
```

---

### Task 3: backend selector in `_open_file_cache`

**Files:**
- Modify: `src/axon/cli/pb.py` (`_open_file_cache`, around line 75)
- Test: `tests/cli/test_open_file_cache_backend.py`

**Interfaces:**
- Consumes: `RuntimeConfig.fileindex_backend`, `PostgresFileCache`.
- Produces: `_open_file_cache()` returns `(PostgresFileCache, PostgresFileCache)` when `fileindex_backend=postgres` (the second element's `close()` closes the pool), else the existing `(SqliteFileCache, aiosqlite_conn)`.

- [ ] **Step 1: Write the failing test (no live backend - fake the cache)**

```python
# tests/cli/test_open_file_cache_backend.py
from __future__ import annotations

import dataclasses

import pytest


async def test_open_file_cache_selects_postgres(monkeypatch) -> None:
    from axon.cli import pb

    constructed = {}

    class FakePgFileCache:
        def __init__(self, dsn: str) -> None:
            constructed["dsn"] = dsn

        async def ensure_schema(self) -> None:
            constructed["ensured"] = True

        async def close(self) -> None:
            constructed["closed"] = True

    monkeypatch.setattr("axon.store.pg_file_cache.PostgresFileCache", FakePgFileCache)
    monkeypatch.setattr(
        pb, "_RUNTIME", dataclasses.replace(pb._RUNTIME, fileindex_backend="postgres")
    )

    cache, handle = await pb._open_file_cache()
    assert isinstance(cache, FakePgFileCache)
    assert constructed["ensured"] is True
    await handle.close()
    assert constructed["closed"] is True


async def test_open_file_cache_defaults_to_sqlite(monkeypatch, tmp_path) -> None:
    from axon.cli import pb
    from axon.store.file_cache import SqliteFileCache

    monkeypatch.setattr(pb, "_get_db_path", lambda: tmp_path / "axon.db")
    monkeypatch.setattr(
        pb, "_RUNTIME", dataclasses.replace(pb._RUNTIME, fileindex_backend="sqlite")
    )
    cache, conn = await pb._open_file_cache()
    try:
        assert isinstance(cache, SqliteFileCache)
    finally:
        await conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/cli/test_open_file_cache_backend.py -v`
Expected: FAIL (selector still always returns SqliteFileCache; the postgres test fails).

- [ ] **Step 3: Make `_open_file_cache` backend-aware**

In `src/axon/cli/pb.py`, change `_open_file_cache()` to branch on the backend at the top, keeping the existing sqlite body unchanged below:

```python
async def _open_file_cache() -> tuple[object, object]:
    """Open a FileCache backed by the configured backend (sqlite or postgres).

    Returns (FileCache, close-handle) - caller must await handle.close().
    """
    if _RUNTIME.fileindex_backend == "postgres":
        from axon.store.pg_file_cache import PostgresFileCache

        cache = PostgresFileCache(dsn=_RUNTIME.pg_url)
        await cache.ensure_schema()
        return cache, cache  # cache.close() closes the pool

    import asyncio as _asyncio

    import aiosqlite

    from axon.store.file_cache import SqliteFileCache
    from axon.store.session_store import _apply_migrations

    db_path = _get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_conn = await aiosqlite.connect(str(db_path))
    await _apply_migrations(db_conn)
    db_lock = _asyncio.Lock()
    return SqliteFileCache(db_conn, db_lock), db_conn
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/cli/test_open_file_cache_backend.py -v`
Expected: 2 passed.

- [ ] **Step 5: Confirm the 5 callers only close the handle (no regression)**

Run: `.venv/Scripts/python.exe -m pytest tests/cli/test_pb_cli.py -q -p no:cacheprovider 2>&1 | tail -5`
Expected: green (callers do `file_cache, db_conn = await _open_file_cache()` then `await db_conn.close()`; both backends honor `await ...close()`).

- [ ] **Step 6: Commit**

```bash
git add src/axon/cli/pb.py tests/cli/test_open_file_cache_backend.py
git commit -m "feat(fileindex): _open_file_cache selects backend by runtime.fileindex_backend"
```

---

### Task 4: Cutover - validate + flip the default (controller-run)

**Files:**
- Modify: `src/axon/config/runtime.py` (`_resolve_fileindex_backend` default)
- Modify: `tests/config/test_fileindex_backend.py`
- Docs: `docs/MIGRATION.md` (file_index wave note)

This task is GATED on operator-run validation that needs Postgres + the vault. Validate FIRST, only then flip.

- [ ] **Step 1: Acceptance gate (operator-run)**

```bash
docker compose up -d axon-postgres
# full index into the Postgres file_index (empty -> rebuilds; uses a throwaway
# engine cache only if you want to avoid mutating the real sqlite db - here we
# index normally with the postgres file_index selected):
AXON_FILEINDEX_BACKEND=postgres AXON_PG_URL="postgresql://axon:axon@localhost:5433/axon" \
  PYTHONPATH=src .venv/Scripts/python.exe -m axon.cli.pb index --ctx knowledge
# verify: file_index populated in Postgres
docker compose exec -T axon-postgres psql -U axon -d axon -tAc "SELECT count(*) FROM file_index;"
# second index must DEDUP (skip unchanged) - re-run and confirm it processes 0 new files
AXON_FILEINDEX_BACKEND=postgres AXON_PG_URL="..." PYTHONPATH=src .venv/Scripts/python.exe -m axon.cli.pb index --ctx knowledge
```
Proceed only if the file_index is populated and the second run dedups. If not, STOP and investigate; do not flip.

- [ ] **Step 2: Update the default test (RED for the flip)**

In `tests/config/test_fileindex_backend.py`, change `test_fileindex_backend_defaults_to_sqlite` to expect postgres and rename:

```python
def test_fileindex_backend_defaults_to_postgres(monkeypatch) -> None:
    monkeypatch.delenv("AXON_FILEINDEX_BACKEND", raising=False)
    from axon.config.runtime import load_runtime_config

    assert load_runtime_config().fileindex_backend == "postgres"
```

- [ ] **Step 3: Run to verify it FAILS (default still sqlite)**

Run: `.venv/Scripts/python.exe -m pytest tests/config/test_fileindex_backend.py::test_fileindex_backend_defaults_to_postgres -v`
Expected: FAIL.

- [ ] **Step 4: Flip the default**

In `src/axon/config/runtime.py`, `_resolve_fileindex_backend`, change the fallback `"sqlite"` to `"postgres"`:

```python
    raw = (
        os.environ.get("AXON_FILEINDEX_BACKEND")
        or overrides.get("fileindex_backend")
        or "postgres"
    )
```

(Leave the `RuntimeConfig.fileindex_backend = "sqlite"` field default as the fixture fallback.)

- [ ] **Step 5: Run to verify pass + sweep**

Run: `.venv/Scripts/python.exe -m pytest tests/config/test_fileindex_backend.py tests/cli/test_open_file_cache_backend.py tests/cli/test_pb_cli.py -q -p no:cacheprovider`
Expected: green (default now postgres; env override to sqlite still rolls back).

- [ ] **Step 6: Runbook + commit**

Add a `docs/MIGRATION.md` note: file_index is a cache (no data copy); flipping to postgres re-builds it on the next index (full re-index once); rollback = `fileindex_backend = "sqlite"` (the SQLite file_index is untouched and reconciles on the next sqlite index). Mixed-backend during step 3 is expected.

```bash
git add src/axon/config/runtime.py tests/config/test_fileindex_backend.py docs/MIGRATION.md
git commit -m "feat(fileindex): cutover - default file_index backend is now postgres (sqlite via override/rollback)"
```

---

## Notes for the executor

- Tasks 1-3 are autonomous (config, PostgresFileCache, selector). Task 4 Step 1 is operator-run (needs Postgres + the vault); the flip (Steps 2-6) follows only after it passes.
- The default flip is deliberately the LAST change. Until Task 4, `fileindex_backend` defaults to sqlite so nothing silently switches.
- Do NOT touch SessionStore's own connection or its non-file_index methods - graph/decisions/sessions stay on SQLite this wave.
- `PostgresFileCache` behavior must remain identical to `SqliteFileCache` - the index pipeline's crash-safety (pending/done) and dedup (sha1) depend on it.
