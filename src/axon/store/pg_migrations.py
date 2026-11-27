"""Versioned Postgres migration runner (MS-4 / #30).

Mirrors the SQLite ``SessionStore._apply_migrations`` contract for asyncpg:

- a ``schema_version(version text PRIMARY KEY, applied_at timestamptz)`` table tracks
  which migrations have run,
- ``*.sql`` files in the migrations directory are applied in filename order,
- each file's stem is recorded so re-running is a no-op (idempotent),
- a new versioned file is applied exactly once on the next run.

The session baseline DDL lives in ``migrations/pg/0001_session_baseline.sql``.
graph / decisions / file_index Postgres paths can adopt this same runner later
(see PR for #30); they are intentionally left untouched for now.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import asyncpg

# pg-specific migrations live in their own subdir so they never collide with the
# SQLite migrations one directory up.
PG_MIGRATIONS_DIR = Path(__file__).parent / "migrations" / "pg"

# Serializes concurrent initializers (#187).
_MIGRATION_LOCK_KEY = 7101870187
# Bounds each lock wait, the advisory one included, so it must come first in the
# transaction. A longer wait raises LockNotAvailableError and records nothing.
_SET_LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"


async def apply_pg_migrations(
    con: asyncpg.Connection, migrations_dir: Path = PG_MIGRATIONS_DIR
) -> None:
    """Apply pending Postgres SQL migrations in filename order.

    Tracks applied versions in ``schema_version`` (version = file stem). Already
    applied versions are skipped, so calling this repeatedly is safe. Each
    migration runs inside a transaction together with its bookkeeping insert so a
    failure leaves no half-applied version recorded. One process at a time applies
    a file, and no lock is waited on for more than 5 seconds.
    """
    async with con.transaction():
        await con.execute(_SET_LOCK_TIMEOUT)
        await con.execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            " version text PRIMARY KEY, applied_at timestamptz NOT NULL)"
        )
    rows = await con.fetch("SELECT version FROM schema_version")
    applied = {r["version"] for r in rows}

    for path in sorted(migrations_dir.glob("*.sql")):
        if path.stem in applied:
            continue
        sql = path.read_text(encoding="utf-8")
        async with con.transaction():
            await con.execute(_SET_LOCK_TIMEOUT)
            await con.execute("SELECT pg_advisory_xact_lock($1)", _MIGRATION_LOCK_KEY)
            # another process may have applied it while this one waited
            if await con.fetchval("SELECT 1 FROM schema_version WHERE version = $1", path.stem):
                continue
            await con.execute(sql)
            await con.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES ($1, $2)",
                path.stem,
                datetime.now(UTC),
            )
