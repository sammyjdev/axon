"""Lifecycle tests for PostgresSessionRepository (MS-3 / issue #28).

RED tests written first per TDD contract:
  - end_session idempotency (first-close-wins, known-id always returns repo)
  - save_session preserves started_at/ended_at on re-save
  - cross-backend conformance (PG vs SQLite produce identical semantics)

Guard: pytest.importorskip so this file is skipped when testcontainers is absent.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer  # noqa: E402


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


async def _truncate_sessions(repo) -> None:
    pool = await repo._ensure_pool()
    async with pool.acquire() as con:
        await con.execute("TRUNCATE sessions")


# ── Test 1: end_session idempotency (PG) ─────────────────────────────────────

async def test_pg_end_session_returns_repo_when_already_ended(pg_dsn) -> None:
    """end_session on a known id must always return repo.
    The second call must NOT re-stamp ended_at.
    end_session on an unknown id must return None.
    """
    from axon.store.pg_session_repository import PostgresSessionRepository

    repo = PostgresSessionRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        await _truncate_sessions(repo)

        await repo.save_session("s1", "manual", "axon", context_payload="v1")

        # First close
        result1 = await repo.end_session("s1")
        assert result1 == "axon", f"expected 'axon', got {result1!r}"

        # Capture ended_at after first close
        sessions = await repo.all_sessions()
        s = next(r for r in sessions if r["id"] == "s1")
        e0 = s["ended_at"]
        assert e0 is not None, "ended_at should be set after first end_session"

        # Second close - same known id, must still return "axon"
        result2 = await repo.end_session("s1")
        assert result2 == "axon", f"second end_session should still return 'axon', got {result2!r}"

        # ended_at must NOT be re-stamped
        sessions2 = await repo.all_sessions()
        s2 = next(r for r in sessions2 if r["id"] == "s1")
        assert s2["ended_at"] == e0, (
            f"ended_at re-stamped: was {e0!r}, now {s2['ended_at']!r}"
        )

        # Unknown id must return None
        result3 = await repo.end_session("missing")
        assert result3 is None, f"unknown id should return None, got {result3!r}"
    finally:
        await repo.close()


# ── Test 2: save_session preserves lifecycle fields on re-save (PG) ──────────

async def test_pg_save_session_preserves_lifecycle_on_resave(pg_dsn) -> None:
    """Re-saving an existing session must preserve started_at and ended_at,
    while updating agent, repo, and context_payload.
    """
    from axon.store.pg_session_repository import PostgresSessionRepository

    repo = PostgresSessionRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        await _truncate_sessions(repo)

        # Initial save
        await repo.save_session("s1", "manual", "axon", context_payload="v1")
        sessions = await repo.all_sessions()
        s = next(r for r in sessions if r["id"] == "s1")
        t0 = s["started_at"]
        assert t0 is not None

        # End session to set ended_at
        await repo.end_session("s1")
        sessions = await repo.all_sessions()
        s = next(r for r in sessions if r["id"] == "s1")
        e0 = s["ended_at"]
        assert e0 is not None

        # Re-save with different agent/repo/payload
        await repo.save_session("s1", "manual2", "axon2", context_payload="v2")

        # Read back
        sessions = await repo.all_sessions()
        s = next(r for r in sessions if r["id"] == "s1")

        assert s["started_at"] == t0, (
            f"started_at changed: was {t0!r}, now {s['started_at']!r}"
        )
        assert s["ended_at"] == e0, (
            f"ended_at changed: was {e0!r}, now {s['ended_at']!r}"
        )
        assert s["agent"] == "manual2", f"agent not updated: {s['agent']!r}"
        assert s["repo"] == "axon2", f"repo not updated: {s['repo']!r}"
        payload = json.loads(s["context_payload"])
        assert payload == {"recall": "v2"}, f"context_payload not updated: {payload!r}"
    finally:
        await repo.close()


# ── Test 3: cross-backend conformance (PG + SQLite) ──────────────────────────

async def test_session_lifecycle_conformance_across_backends(pg_dsn, tmp_path) -> None:
    """Both backends must produce identical session lifecycle semantics."""
    from axon.store.pg_session_repository import PostgresSessionRepository
    from axon.store.session_repository import SqliteSessionRepository
    from axon.store.session_store import SessionStore

    async def run_lifecycle(repo):
        """Shared script; returns a normalized tuple for comparison."""
        await repo.save_session("conf1", "agent-a", "repo-x", context_payload="p1")
        sessions = await repo.all_sessions()
        s = next(r for r in sessions if r["id"] == "conf1")
        t0 = s["started_at"]

        result = await repo.end_session("conf1")
        assert result == "repo-x"

        sessions = await repo.all_sessions()
        s = next(r for r in sessions if r["id"] == "conf1")
        e0 = s["ended_at"]

        # Re-save - should preserve t0/e0
        await repo.save_session("conf1", "agent-b", "repo-y", context_payload="p2")

        sessions = await repo.all_sessions()
        s = next(r for r in sessions if r["id"] == "conf1")

        started_preserved = s["started_at"] == t0
        ended_preserved = s["ended_at"] == e0
        payload = json.loads(s["context_payload"])

        return (
            s["agent"],       # "agent-b"
            s["repo"],        # "repo-y"
            started_preserved,
            ended_preserved,
            payload,
        )

    # Run on PG
    pg_repo = PostgresSessionRepository(dsn=pg_dsn)
    try:
        await pg_repo.ensure_schema()
        await _truncate_sessions(pg_repo)
        pg_result = await run_lifecycle(pg_repo)
    finally:
        await pg_repo.close()

    # Run on SQLite
    store = SessionStore(db_path=tmp_path / "axon.db")
    await store.init()
    sqlite_repo = SqliteSessionRepository(store)
    try:
        sqlite_result = await run_lifecycle(sqlite_repo)
    finally:
        await store.close()

    assert pg_result == sqlite_result, (
        f"Backend divergence:\n  PG:     {pg_result}\n  SQLite: {sqlite_result}"
    )
