"""Session timestamps are timestamptz, not ISO text (#31 / MS-1).

Text ordering is only correct while every value happens to be uniform-UTC ISO;
one naive or offset-shifted write and ORDER BY DESC silently returns the wrong
row. timestamptz is the same 8 bytes, normalises to UTC, and sorts as an integer.

The migration is a text -> timestamptz cast, which is exactly the class of
conversion that corrupts content while row counts stay identical — the concern
#32 (MS-7) tracked before it was closed as obsolete. The content check lives here.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("testcontainers.postgres")
import asyncpg  # noqa: E402
from testcontainers.postgres import PostgresContainer  # noqa: E402

from axon.store.session_store import CodeChange, SessionMemory, SessionNote  # noqa: E402

_TIMESTAMP_COLUMNS = {
    ("session_memory", "created_at"),
    ("session_note", "created_at"),
    ("code_change", "changed_at"),
    ("sessions", "started_at"),
    ("sessions", "ended_at"),
}


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


async def test_columns_are_timestamptz(pg_dsn) -> None:
    from axon.store.pg_session_repository import PostgresSessionRepository

    repo = PostgresSessionRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
    finally:
        await repo.close()

    con = await asyncpg.connect(pg_dsn)
    try:
        rows = await con.fetch(
            "SELECT table_name, column_name, data_type FROM information_schema.columns"
            " WHERE table_schema='public'"
        )
    finally:
        await con.close()

    types = {(r["table_name"], r["column_name"]): r["data_type"] for r in rows}
    wrong = {
        col: types[col]
        for col in _TIMESTAMP_COLUMNS
        if types.get(col) != "timestamp with time zone"
    }
    assert not wrong, f"still text: {wrong}"


async def test_roundtrip_preserves_the_instant(pg_dsn) -> None:
    """A datetime survives the write/read cycle as the same instant."""
    from axon.store.pg_session_repository import PostgresSessionRepository

    written = datetime(2026, 3, 1, 12, 34, 56, 789012, tzinfo=UTC)
    repo = PostgresSessionRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        await repo.save_session_memory(
            SessionMemory(project="ts", summary="s", raw_turns=1, created_at=written)
        )
        await repo.save_note(SessionNote(project="ts", body="b", created_at=written))
        await repo.save_code_change_inner(
            CodeChange(
                commit_hash="abc123", file_path="f.py", diff_summary="d",
                why="w", changed_at=written,
            )
        )

        mem = (await repo.get_session_memories("ts", limit=1))[0]
        note = (await repo.get_notes("ts", limit=1))[0]
        change = (await repo.get_recent_changes("f.py", limit=1))[0]
    finally:
        await repo.close()

    assert mem.created_at == written
    assert note.created_at == written
    assert change.changed_at == written


async def test_ordering_is_chronological_not_lexicographic(pg_dsn) -> None:
    """The defect text ordering hides: a non-UTC offset sorts wrong as a string.

    "2026-04-01T00:30:00+02:00" is EARLIER than "2026-04-01T00:00:00+00:00" as an
    instant, but LATER as a string. Under text columns the newest-first query
    returns the wrong row.
    """
    from axon.store.pg_session_repository import PostgresSessionRepository

    earlier_instant = datetime(2026, 4, 1, 0, 30, tzinfo=timezone_plus_two())
    later_instant = datetime(2026, 4, 1, 0, 0, tzinfo=UTC)
    assert earlier_instant < later_instant
    assert earlier_instant.isoformat() > later_instant.isoformat()  # the trap

    repo = PostgresSessionRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        await repo.save_note(
            SessionNote(project="ord", body="earlier", created_at=earlier_instant)
        )
        await repo.save_note(
            SessionNote(project="ord", body="later", created_at=later_instant)
        )
        newest_first = await repo.get_notes("ord", limit=2)
    finally:
        await repo.close()

    assert [n.body for n in newest_first] == ["later", "earlier"]


def timezone_plus_two():
    from datetime import timezone

    return timezone(timedelta(hours=2))


async def _reset_to_pre_0004(con) -> None:
    """Rebuild the database at the pre-0004 state: text columns, 0004 pending.

    Runs migrations 0001-0003 for real from a temp directory rather than faking
    schema_version rows, so each upgrade test owns its whole starting state
    instead of depending on what an earlier test happened to leave behind.
    """
    import shutil
    import tempfile
    from pathlib import Path

    from axon.store.pg_migrations import PG_MIGRATIONS_DIR, apply_pg_migrations

    await con.execute(
        "DROP TABLE IF EXISTS session_memory, session_note, code_change, sessions,"
        " schema_version CASCADE"
    )
    with tempfile.TemporaryDirectory() as tmp:
        for src in sorted(PG_MIGRATIONS_DIR.glob("*.sql")):
            if src.stem.startswith("0004"):
                continue
            shutil.copy(src, Path(tmp) / src.name)
        await apply_pg_migrations(con, migrations_dir=Path(tmp))


async def test_cast_preserves_existing_text_values(pg_dsn) -> None:
    """The upgrade path: text rows already in the table survive the cast intact.

    This is the content check #32 (MS-7) asked for. Row counts alone would pass
    a cast that shifted every instant by the server's UTC offset, so the
    assertion is on the values, not the count.
    """
    from axon.store.pg_migrations import apply_pg_migrations

    con = await asyncpg.connect(pg_dsn)
    try:
        await _reset_to_pre_0004(con)

        originals = [
            "2026-06-29T23:33:04.006652+00:00",   # microseconds, UTC
            "2026-05-27T00:00:00+00:00",          # midnight boundary
            "2026-04-01T00:30:00+02:00",          # non-UTC offset
        ]
        for i, raw in enumerate(originals):
            await con.execute(
                "INSERT INTO session_note (project, body, created_at) VALUES ($1, $2, $3)",
                "p", f"body{i}", raw,
            )

        await apply_pg_migrations(con)

        assert await con.fetchval(
            "SELECT data_type FROM information_schema.columns"
            " WHERE table_name='session_note' AND column_name='created_at'"
        ) == "timestamp with time zone"

        rows = await con.fetch("SELECT body, created_at FROM session_note ORDER BY body")
        assert len(rows) == len(originals)
        for row, raw in zip(rows, originals, strict=True):
            assert row["created_at"] == datetime.fromisoformat(raw), (
                f"{row['body']}: {raw} became {row['created_at']}"
            )
    finally:
        await con.close()


async def test_naive_values_cast_as_utc_regardless_of_server_timezone(pg_dsn) -> None:
    """A value stored without an offset must read back as UTC, not server-local.

    Exercises the `SET LOCAL TimeZone = 'UTC'` guard in 0004. Without it, a
    server running e.g. America/Sao_Paulo would read a naive "00:30" as 03:30Z,
    shifting the instant by three hours while row counts stay identical.
    """
    from axon.store.pg_migrations import apply_pg_migrations

    con = await asyncpg.connect(pg_dsn)
    try:
        await _reset_to_pre_0004(con)
        await con.execute("SET TimeZone = 'America/Sao_Paulo'")  # UTC-3, not UTC
        await con.execute(
            "INSERT INTO session_note (project, body, created_at)"
            " VALUES ('p', 'naive', '2026-04-01T00:30:00')"
        )

        await apply_pg_migrations(con)

        stored = await con.fetchval("SELECT created_at FROM session_note WHERE body='naive'")
        assert stored == datetime(2026, 4, 1, 0, 30, tzinfo=UTC), (
            f"naive value read as {stored}; the TimeZone guard in 0004 is not holding"
        )
    finally:
        await con.close()
