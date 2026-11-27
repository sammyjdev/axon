"""#187: legacy rows (no session_id) are reported, never keyed, never changed.

Real testcontainers Postgres; without Docker these error instead of skipping.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("testcontainers.postgres")
import asyncpg  # noqa: E402
from testcontainers.postgres import PostgresContainer  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

from axon.__main__ import app  # noqa: E402
from axon.store.pg_migrations import PG_MIGRATIONS_DIR, apply_pg_migrations  # noqa: E402
from axon.store.pg_session_repository import PostgresSessionRepository  # noqa: E402
from axon.store.session_store import SessionMemory  # noqa: E402

T0 = datetime(2026, 1, 1, tzinfo=UTC)
CHECKSUM = (
    "SELECT md5(string_agg(id || '|' || project || '|' || md5(summary) || '|' || raw_turns"
    " || '|' || created_at::text, ',' ORDER BY id)) FROM session_memory WHERE id = ANY($1)"
)
runner = CliRunner()


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


async def _legacy_schema(pg_dsn: str, tmp_path: Path) -> None:
    """The schema as it was before 0008."""
    old = tmp_path / "pre0008"
    old.mkdir()
    for path in PG_MIGRATIONS_DIR.glob("*.sql"):
        if path.name < "0008":
            shutil.copy(path, old / path.name)
    con = await asyncpg.connect(pg_dsn)
    try:
        await con.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        await apply_pg_migrations(con, old)
    finally:
        await con.close()


async def _insert_legacy(pg_dsn: str, rows: list[tuple]) -> list[int]:
    con = await asyncpg.connect(pg_dsn)
    try:
        return [
            await con.fetchval(
                "INSERT INTO session_memory (project, summary, raw_turns, created_at)"
                " VALUES ($1, $2, $3, $4) RETURNING id",
                *row,
            )
            for row in rows
        ]
    finally:
        await con.close()


async def _query(pg_dsn: str, sql: str, *args) -> list[dict]:
    con = await asyncpg.connect(pg_dsn)
    try:
        return [dict(r) for r in await con.fetch(sql, *args)]
    finally:
        await con.close()


async def _all_rows(pg_dsn: str) -> list[dict]:
    return await _query(pg_dsn, "SELECT * FROM session_memory ORDER BY id")


def _minutes(n: int) -> datetime:
    return T0 + timedelta(minutes=n)


async def test_report_changes_nothing(pg_dsn, tmp_path, monkeypatch) -> None:
    await _legacy_schema(pg_dsn, tmp_path)
    ids = await _insert_legacy(
        pg_dsn,
        [
            ("p", "dup", 3, _minutes(1)),  # 0 extra
            ("p", "dup", 3, _minutes(3)),  # 1 keep: newest
            ("p", "dup", 3, _minutes(2)),  # 2 extra
            ("p", "dup", 4, _minutes(4)),  # 3 other raw_turns
            ("p", "dup ", 3, _minutes(5)),  # 4 trailing space
            ("q", "tie", 1, _minutes(6)),  # 5 extra
            ("q", "tie", 1, _minutes(7)),  # 6 keep
            ("r", "single", 1, _minutes(8)),
        ],
    )
    repo = PostgresSessionRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        # rows with a session id are never in a group
        for sid in ("k-1", "k-2"):
            await repo.save_session_memory(
                SessionMemory(project="p", summary="dup", raw_turns=3, session_id=sid)
            )
    finally:
        await repo.close()
    before = await _all_rows(pg_dsn)

    monkeypatch.setenv("AXON_PG_URL", pg_dsn)
    result = await asyncio.to_thread(runner.invoke, app, ["session", "duplicates"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        f"p  keep {ids[1]}  extra {ids[2]},{ids[0]}",
        f"q  keep {ids[6]}  extra {ids[5]}",
        "2 duplicate group(s), 3 extra row(s); nothing changed",
    ]
    assert await _all_rows(pg_dsn) == before
    backup = await _query(
        pg_dsn, "SELECT to_regclass('session_memory_dedupe_backup') AS t"
    )
    assert backup[0]["t"] is None


async def test_no_legacy_row_is_changed(pg_dsn, tmp_path, monkeypatch) -> None:
    await _legacy_schema(pg_dsn, tmp_path)
    for name in list(os.environ):
        if name.startswith("GIT_") and name not in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"):
            monkeypatch.delenv(name)
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)  # noqa: S607
    ids = await _insert_legacy(
        pg_dsn,
        [("repo", f"legacy {i % 7}", i % 3 + 1, _minutes(i)) for i in range(30)],
    )
    before = (await _query(pg_dsn, CHECKSUM, ids))[0]
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        "".join(
            json.dumps({"type": r, "message": {"role": r, "content": c}}) + "\n"
            for r, c in (("user", "legacy 1"), ("assistant", "done"))
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AXON_PG_URL", pg_dsn)
    payload = json.dumps(
        {"session_id": "new-1", "transcript_path": str(transcript), "cwd": str(repo_dir)}
    )

    for _ in range(3):
        result = await asyncio.to_thread(runner.invoke, app, ["session-hook"], input=payload)
        assert result.exit_code == 0, result.output
    report = await asyncio.to_thread(runner.invoke, app, ["session", "duplicates"])

    assert report.exit_code == 0, report.output
    assert (await _query(pg_dsn, CHECKSUM, ids))[0] == before
    rows = await _all_rows(pg_dsn)
    assert all(r["session_id"] is None for r in rows if r["id"] in ids)
    assert [r["session_id"] for r in rows if r["id"] not in ids] == ["new-1"]


async def test_keyed_sessions_lead_recall(pg_dsn, tmp_path) -> None:
    await _legacy_schema(pg_dsn, tmp_path)
    await _insert_legacy(pg_dsn, [("p", "near dup", 3, _minutes(i)) for i in range(5)])
    repo = PostgresSessionRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        for i in range(3):
            await repo.save_session_memory(
                SessionMemory(
                    project="p",
                    summary=f"session {i}",
                    raw_turns=2,
                    created_at=_minutes(100 + i),
                    session_id=f"k-{i}",
                )
            )
        recalled = await repo.get_session_memories("p", limit=3)
    finally:
        await repo.close()

    assert [m.session_id for m in recalled] == ["k-2", "k-1", "k-0"]
