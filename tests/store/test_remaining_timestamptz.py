"""Every timestamp column is timestamptz, not ISO text (DEBT-1, follow-up to #31).

#31 / migration 0004 converted the five session columns. Nine more carried the
same defect across graph, decisions, adr, file_index, the failure/outcome stores,
and - most pointedly - `schema_version.applied_at`, the migration runner's own
bookkeeping.

Ordering hazard this covers: the repositories are lazy, so a table may not exist
yet when 0005 runs. The migration therefore uses ALTER TABLE IF EXISTS, and the
inline DDL in each repository creates the column as timestamptz so a table born
later is already correct.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

pytest.importorskip("testcontainers.postgres")
import asyncpg  # noqa: E402
from testcontainers.postgres import PostgresContainer  # noqa: E402

_EXPECTED = {
    ("schema_version", "applied_at"),
    ("nodes", "created_at"),
    ("nodes", "updated_at"),
    ("edges", "created_at"),
    ("decisions", "created_at"),
    ("adr", "created_at"),
    ("file_index", "indexed_at"),
    ("failure_record", "created_at"),
    ("outcome_record", "created_at"),
}


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


async def _ensure_every_schema(dsn: str) -> None:
    """Init every repository that owns one of the columns under test."""
    from axon.store.failure_store import FailureStore
    from axon.store.outcome_store import OutcomeStore
    from axon.store.pg_decision_repository import PostgresDecisionRepository
    from axon.store.pg_file_cache import PostgresFileCache
    from axon.store.pg_graph_repository import PostgresGraphRepository
    from axon.store.pg_session_repository import PostgresSessionRepository

    for factory in (
        PostgresGraphRepository,
        PostgresDecisionRepository,
        PostgresFileCache,
        FailureStore,
        OutcomeStore,
        PostgresSessionRepository,  # last: runs apply_pg_migrations
    ):
        repo = factory(dsn)
        try:
            # FailureStore/OutcomeStore name it init(); the repositories use
            # ensure_schema().
            init = getattr(repo, "ensure_schema", None) or repo.init
            await init()
        finally:
            close = getattr(repo, "close", None)
            if close:
                await close()


async def test_no_timestamp_column_is_text(pg_dsn) -> None:
    await _ensure_every_schema(pg_dsn)

    con = await asyncpg.connect(pg_dsn)
    try:
        rows = await con.fetch(
            "SELECT table_name, column_name, data_type FROM information_schema.columns"
            " WHERE table_schema='public'"
        )
    finally:
        await con.close()

    types = {(r["table_name"], r["column_name"]): r["data_type"] for r in rows}
    missing = {c for c in _EXPECTED if c not in types}
    assert not missing, f"tables never created: {missing}"

    wrong = {c: types[c] for c in _EXPECTED if types[c] != "timestamp with time zone"}
    assert not wrong, f"still text: {wrong}"


async def test_migration_survives_a_table_that_does_not_exist_yet(pg_dsn) -> None:
    """Lazy repositories mean 0005 can run before a table has been created."""
    from axon.store.pg_migrations import apply_pg_migrations

    await _ensure_every_schema(pg_dsn)  # make sure schema_version exists
    con = await asyncpg.connect(pg_dsn)
    try:
        await con.execute("DROP TABLE IF EXISTS nodes, edges, file_index CASCADE")
        await con.execute("DELETE FROM schema_version WHERE version LIKE '0005%'")

        await apply_pg_migrations(con)  # must not raise on the absent tables

        applied = await con.fetchval(
            "SELECT count(*) FROM schema_version WHERE version LIKE '0005%'"
        )
        assert applied == 1
    finally:
        await con.close()


async def test_runner_records_applied_at_as_a_real_instant(pg_dsn) -> None:
    """schema_version.applied_at is written by the runner itself."""
    await _ensure_every_schema(pg_dsn)

    con = await asyncpg.connect(pg_dsn)
    try:
        value = await con.fetchval("SELECT applied_at FROM schema_version LIMIT 1")
    finally:
        await con.close()

    assert isinstance(value, datetime), f"applied_at came back as {type(value).__name__}"
    assert value.tzinfo is not None


async def test_cast_preserves_existing_text_values(pg_dsn) -> None:
    """Content check, same as #31: values must survive, not just row counts."""
    from axon.store.pg_migrations import apply_pg_migrations

    originals = [
        "2026-06-29T23:33:04.006652+00:00",
        "2026-05-27T00:00:00+00:00",
        "2026-04-01T00:30:00+02:00",
    ]
    await _ensure_every_schema(pg_dsn)  # make sure schema_version exists
    con = await asyncpg.connect(pg_dsn)
    try:
        await con.execute("DROP TABLE IF EXISTS file_index CASCADE")
        await con.execute("DELETE FROM schema_version WHERE version LIKE '0005%'")
        await con.execute(
            "CREATE TABLE file_index (path text PRIMARY KEY, content_hash text NOT NULL,"
            " indexed_at text NOT NULL)"
        )
        for i, raw in enumerate(originals):
            await con.execute(
                "INSERT INTO file_index (path, content_hash, indexed_at) VALUES ($1, 'h', $2)",
                f"/f{i}.py",
                raw,
            )

        await apply_pg_migrations(con)

        rows = await con.fetch("SELECT path, indexed_at FROM file_index ORDER BY path")
        assert len(rows) == len(originals)
        for row, raw in zip(rows, originals, strict=True):
            assert row["indexed_at"] == datetime.fromisoformat(raw), (
                f"{row['path']}: {raw} became {row['indexed_at']}"
            )
    finally:
        await con.close()
