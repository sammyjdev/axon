"""MS-4 (#30): versioned Postgres migration runner mirroring the SQLite
``_apply_migrations`` contract. Real testcontainers Postgres, importorskip."""
from __future__ import annotations

import asyncio
import shutil
import time
import warnings
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("testcontainers.postgres")
import asyncpg  # noqa: E402
from testcontainers.postgres import PostgresContainer  # noqa: E402

from axon.store.pg_migrations import (  # noqa: E402
    PG_MIGRATIONS_DIR,
    apply_pg_migrations,
)


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


async def _table_exists(con: asyncpg.Connection, table: str) -> bool:
    return await con.fetchval("SELECT to_regclass($1) IS NOT NULL", f"public.{table}")


async def test_runner_creates_schema_version_and_applies_baseline(pg_dsn) -> None:
    """AC1+AC2: runner makes a schema_version table and applies 0001 baseline."""
    pool = await asyncpg.create_pool(pg_dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as con:
            await con.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
            await apply_pg_migrations(con, PG_MIGRATIONS_DIR)

            # schema_version table exists with the documented shape.
            assert await _table_exists(con, "schema_version")
            cols = {
                r["column_name"]: r["data_type"]
                for r in await con.fetch(
                    "SELECT column_name, data_type FROM information_schema.columns"
                    " WHERE table_name='schema_version'"
                )
            }
            assert cols.get("version") == "text"
            assert "applied_at" in cols

            # The four session baseline tables exist after the runner.
            for table in ("session_memory", "session_note", "code_change", "sessions"):
                assert await _table_exists(con, table), table

            # 0001 is recorded as applied.
            applied = {
                r["version"] for r in await con.fetch("SELECT version FROM schema_version")
            }
            assert any(v.startswith("0001") for v in applied)
    finally:
        await pool.close()


async def test_runner_is_idempotent(pg_dsn) -> None:
    """AC3 part 1: running twice is a no-op (no duplicate rows, no errors)."""
    pool = await asyncpg.create_pool(pg_dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as con:
            await con.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
            await apply_pg_migrations(con, PG_MIGRATIONS_DIR)
            count1 = await con.fetchval("SELECT count(*) FROM schema_version")
            await apply_pg_migrations(con, PG_MIGRATIONS_DIR)
            count2 = await con.fetchval("SELECT count(*) FROM schema_version")
            assert count1 == count2 and count1 >= 1
    finally:
        await pool.close()


async def test_new_migration_applied_exactly_once(pg_dsn, tmp_path: Path) -> None:
    """AC3 part 2: a NEW 0002 migration applies once on next run, not twice."""
    # Copy the real migrations into a temp dir and add a dummy 0002.
    src_files = sorted(PG_MIGRATIONS_DIR.glob("*.sql"))
    for f in src_files:
        (tmp_path / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "0002_ms4_probe.sql").write_text(
        "CREATE TABLE IF NOT EXISTS ms4_probe (id integer PRIMARY KEY);\n",
        encoding="utf-8",
    )

    pool = await asyncpg.create_pool(pg_dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as con:
            await con.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
            # First run: baseline only (real dir), probe absent.
            await apply_pg_migrations(con, PG_MIGRATIONS_DIR)
            assert not await _table_exists(con, "ms4_probe")

            # Run against temp dir: 0001 already applied -> only 0002 runs.
            await apply_pg_migrations(con, tmp_path)
            assert await _table_exists(con, "ms4_probe")
            probe_rows = await con.fetchval(
                "SELECT count(*) FROM schema_version WHERE version=$1", "0002_ms4_probe"
            )
            assert probe_rows == 1

            # Running the temp dir again is a no-op for 0002.
            await apply_pg_migrations(con, tmp_path)
            probe_rows2 = await con.fetchval(
                "SELECT count(*) FROM schema_version WHERE version=$1", "0002_ms4_probe"
            )
            assert probe_rows2 == 1
    finally:
        await pool.close()


async def test_ensure_schema_delegates_to_runner(pg_dsn) -> None:
    """AC2: PostgresSessionRepository.ensure_schema runs the versioned runner."""
    from axon.store.pg_session_repository import PostgresSessionRepository

    pool = await asyncpg.create_pool(pg_dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as con:
            await con.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    finally:
        await pool.close()

    repo = PostgresSessionRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        await repo.ensure_schema()  # idempotent
        async with (await repo._ensure_pool()).acquire() as con:
            assert await _table_exists(con, "schema_version")
            for table in ("session_memory", "session_note", "code_change", "sessions"):
                assert await _table_exists(con, table), table
            # baseline recorded
            applied = {
                r["version"] for r in await con.fetch("SELECT version FROM schema_version")
            }
            assert any(v.startswith("0001") for v in applied)
    finally:
        await repo.close()


# ── 0008: session_memory.session_id (#187) ───────────────────────────────────

M0008 = "0008_session_memory_session_id"
LOCK_KEY = 7101870187


async def _schema_before_0008(con: asyncpg.Connection, tmp_path: Path, rows: int = 0) -> None:
    old = tmp_path / "pre0008"
    old.mkdir(exist_ok=True)
    for path in PG_MIGRATIONS_DIR.glob("*.sql"):
        if path.name < "0008":
            shutil.copy(path, old / path.name)
    await con.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    await apply_pg_migrations(con, old)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    if rows:
        await con.executemany(
            "INSERT INTO session_memory (project, summary, raw_turns, created_at)"
            " VALUES ($1, $2, $3, $4)",
            [
                (f"p{i % 5}", f"summary {i % 235} " * 40, i % 4, start + timedelta(seconds=i))
                for i in range(rows)
            ],
        )


async def _recorded(con: asyncpg.Connection) -> int:
    return await con.fetchval("SELECT count(*) FROM schema_version WHERE version=$1", M0008)


async def test_0008_adds_session_id_without_touching_rows(pg_dsn, tmp_path: Path) -> None:
    con = await asyncpg.connect(pg_dsn)
    try:
        await _schema_before_0008(con, tmp_path, rows=30)
        checksum = (
            "SELECT md5(string_agg(id || '|' || project || '|' || md5(summary) || '|' ||"
            " raw_turns || '|' || created_at::text, ',' ORDER BY id)) FROM session_memory"
        )
        before = await con.fetchval(checksum)
        versions = await con.fetchval("SELECT count(*) FROM schema_version")

        await apply_pg_migrations(con, PG_MIGRATIONS_DIR)
        await apply_pg_migrations(con, PG_MIGRATIONS_DIR)

        assert await _recorded(con) == 1
        assert await con.fetchval("SELECT count(*) FROM schema_version") == versions + 1
        assert await con.fetchval(checksum) == before
        assert await con.fetchval(
            "SELECT count(*) FROM session_memory WHERE session_id IS NULL"
            " AND compact_summary IS NULL AND compact_created_at IS NULL"
            " AND digest_summary IS NULL AND digest_raw_turns IS NULL"
            " AND digest_created_at IS NULL"
        ) == 30
        indexes = {
            r["indexname"]: r["indexdef"]
            for r in await con.fetch(
                "SELECT indexname, indexdef FROM pg_indexes WHERE tablename='session_memory'"
            )
        }
        assert "UNIQUE" in indexes["uq_session_memory_session_id"]
        assert "WHERE (session_id IS NOT NULL)" in indexes["uq_session_memory_session_id"]
        assert "UNIQUE" in indexes["uq_session_memory_natural"]
        assert "WHERE (session_id IS NULL)" in indexes["uq_session_memory_natural"]
    finally:
        await con.close()


async def test_concurrent_initializers_apply_0008_once(pg_dsn, tmp_path: Path) -> None:
    holder, one, two = [await asyncpg.connect(pg_dsn) for _ in range(3)]
    try:
        await _schema_before_0008(holder, tmp_path)
        # hold the migration lock so both initializers have seen 0008 as pending
        await holder.execute(f"SELECT pg_advisory_lock({LOCK_KEY})")
        tasks = [asyncio.create_task(apply_pg_migrations(c)) for c in (one, two)]
        await asyncio.sleep(1)
        await holder.execute(f"SELECT pg_advisory_unlock({LOCK_KEY})")

        await asyncio.wait_for(asyncio.gather(*tasks), 30)

        assert await _recorded(holder) == 1
    finally:
        for con in (holder, one, two):
            await con.close()


async def test_blocked_migration_times_out_and_records_nothing(pg_dsn, tmp_path: Path) -> None:
    holder, con = [await asyncpg.connect(pg_dsn) for _ in range(2)]
    try:
        await _schema_before_0008(con, tmp_path)
        reader = holder.transaction()
        await reader.start()
        await holder.execute("LOCK TABLE session_memory IN ACCESS SHARE MODE")

        started = time.monotonic()
        with pytest.raises(asyncpg.exceptions.LockNotAvailableError):
            await asyncio.wait_for(apply_pg_migrations(con), 20)

        assert time.monotonic() - started < 6
        assert await _recorded(con) == 0
        await reader.rollback()
    finally:
        await holder.close()
        await con.close()


async def test_held_advisory_lock_times_out(pg_dsn, tmp_path: Path) -> None:
    holder, con = [await asyncpg.connect(pg_dsn) for _ in range(2)]
    try:
        await _schema_before_0008(con, tmp_path)
        await holder.execute(f"SELECT pg_advisory_lock({LOCK_KEY})")

        started = time.monotonic()
        with pytest.raises(asyncpg.exceptions.LockNotAvailableError):
            await asyncio.wait_for(apply_pg_migrations(con), 20)

        assert time.monotonic() - started < 6
        assert await _recorded(con) == 0

        await holder.execute(f"SELECT pg_advisory_unlock({LOCK_KEY})")
        await apply_pg_migrations(con)
        assert await _recorded(con) == 1
    finally:
        await holder.close()
        await con.close()


async def test_0008_on_1062_rows_is_short(pg_dsn, tmp_path: Path) -> None:
    """The size #187 reports. The warning puts the measured time in the CI log."""
    con = await asyncpg.connect(pg_dsn)
    try:
        await _schema_before_0008(con, tmp_path, rows=1062)

        started = time.monotonic()
        await apply_pg_migrations(con)
        elapsed = time.monotonic() - started

        warnings.warn(f"migration 0008 on 1062 rows took {elapsed * 1000:.0f} ms", stacklevel=1)
        assert await _recorded(con) == 1
        assert elapsed < 5
    finally:
        await con.close()
