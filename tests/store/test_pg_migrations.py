"""MS-4 (#30): versioned Postgres migration runner mirroring the SQLite
``_apply_migrations`` contract. Real testcontainers Postgres, importorskip."""
from __future__ import annotations

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
