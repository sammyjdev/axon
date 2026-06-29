from __future__ import annotations

import json
import sqlite3

import pytest

pytest.importorskip("testcontainers.postgres")
import asyncpg  # noqa: E402
from testcontainers.postgres import PostgresContainer  # noqa: E402

from axon.store.decision_backfill import run_backfill  # noqa: E402
from axon.store.pg_decision_repository import PostgresDecisionRepository  # noqa: E402


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


def _fm(did, repo="axon", git_hash="", summary="s"):
    return {
        "id": did, "timestamp": "2026-01-01T00:00:00+00:00", "agent": "manual",
        "repo": repo, "files": [], "symbols": [], "summary": summary,
        "validation_score": 0.0, "judged": False, "git_hash": git_hash or None,
        "linked_decisions": [], "tags": [], "status": "draft",
    }


def _make_sqlite(path, decisions, adrs):
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE decisions (id TEXT PRIMARY KEY, frontmatter TEXT NOT NULL,"
        " body TEXT NOT NULL DEFAULT '', vault_path TEXT, created_at TEXT NOT NULL)"
    )
    con.execute(
        "CREATE TABLE adr (id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL,"
        " title TEXT NOT NULL, context TEXT NOT NULL, decision TEXT NOT NULL,"
        " rationale TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    for fm in decisions:
        con.execute(
            "INSERT INTO decisions (id, frontmatter, body, created_at) VALUES (?,?,?,?)",
            (fm["id"], json.dumps(fm), fm["summary"], fm["timestamp"]),
        )
    for a in adrs:
        con.execute(
            "INSERT INTO adr (project, title, context, decision, rationale, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (a["project"], a["title"], "ctx", "dec", "rat", a["created_at"]),
        )
    con.commit()
    con.close()


async def _pg_ids(dsn):
    con = await asyncpg.connect(dsn)
    try:
        rows = await con.fetch("SELECT id FROM decisions ORDER BY id")
        return [r["id"] for r in rows]
    finally:
        await con.close()


async def test_backfill_copies_legacy_and_renumbers_native(pg_dsn, tmp_path):
    # PG seeded with two native rows dec-001/dec-002 (different git hashes)
    repo = PostgresDecisionRepository(dsn=pg_dsn)
    await repo.ensure_schema()
    con = await asyncpg.connect(pg_dsn)
    try:
        await con.execute("DELETE FROM decisions")
        await con.execute("DELETE FROM adr")
        for did, gh in [("dec-001", "native-a"), ("dec-002", "native-b")]:
            await con.execute(
                "INSERT INTO decisions (id, frontmatter, body, created_at) "
                "VALUES ($1,$2::jsonb,$3,$4)",
                did, json.dumps(_fm(did, git_hash=gh)), "s", "2026-01-01T00:00:00+00:00",
            )
    finally:
        await con.close()

    sqlite_path = str(tmp_path / "legacy.db")
    _make_sqlite(
        sqlite_path,
        [
            _fm("dec-001", git_hash="h1"),
            _fm("dec-002", git_hash="h2"),
            _fm("dec-003", git_hash="h3"),
        ],
        [{"project": "axon", "title": "ADR-1", "created_at": "2026-01-01T00:00:00+00:00"}],
    )

    report = await run_backfill(sqlite_path, pg_dsn, dry_run=False)

    assert report.copied_decisions == 3
    assert report.renumbered == (("dec-001", "dec-004"), ("dec-002", "dec-005"))
    assert report.copied_adrs == 1
    ids = await _pg_ids(pg_dsn)
    assert ids == ["dec-001", "dec-002", "dec-003", "dec-004", "dec-005"]
    # the renumbered rows kept native content; legacy ids carry legacy git hashes
    con = await asyncpg.connect(pg_dsn)
    try:
        g1 = await con.fetchval("SELECT frontmatter->>'git_hash' FROM decisions WHERE id='dec-001'")
        g4 = await con.fetchval("SELECT frontmatter->>'git_hash' FROM decisions WHERE id='dec-004'")
        id4 = await con.fetchval("SELECT frontmatter->>'id' FROM decisions WHERE id='dec-004'")
    finally:
        await con.close()
    assert g1 == "h1" and g4 == "native-a" and id4 == "dec-004"


async def test_backfill_is_idempotent(pg_dsn, tmp_path):
    sqlite_path = str(tmp_path / "legacy2.db")
    _make_sqlite(sqlite_path, [_fm("dec-001", git_hash="h1")], [])
    con = await asyncpg.connect(pg_dsn)
    try:
        await con.execute("DELETE FROM decisions")
        await con.execute("DELETE FROM adr")
    finally:
        await con.close()

    await run_backfill(sqlite_path, pg_dsn, dry_run=False)
    before = await _pg_ids(pg_dsn)
    second = await run_backfill(sqlite_path, pg_dsn, dry_run=False)
    after = await _pg_ids(pg_dsn)
    assert before == after  # no new rows, no renumber churn
    assert second.renumbered == ()


async def test_dry_run_writes_nothing(pg_dsn, tmp_path):
    sqlite_path = str(tmp_path / "legacy3.db")
    _make_sqlite(sqlite_path, [_fm("dec-001", git_hash="h1")], [])
    con = await asyncpg.connect(pg_dsn)
    try:
        await con.execute("DELETE FROM decisions")
        await con.execute("DELETE FROM adr")
    finally:
        await con.close()
    report = await run_backfill(sqlite_path, pg_dsn, dry_run=True)
    assert report.dry_run is True and report.copied_decisions == 1
    assert await _pg_ids(pg_dsn) == []  # nothing written
