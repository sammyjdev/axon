"""SQLite-only lifecycle conformance tests (MS-3 / issue #28).

No importorskip - runs in the default suite without Docker.

Test 4: resave does not reopen or reset started_at.
  This FAILS on the current INSERT OR REPLACE implementation,
  which resets started_at and clears ended_at on re-save.
"""
from __future__ import annotations

import pytest

from axon.store.session_repository import SqliteSessionRepository
from axon.store.session_store import SessionStore


@pytest.fixture()
async def sqlite_repo(tmp_path):
    store = SessionStore(db_path=tmp_path / "axon.db")
    await store.init()
    repo = SqliteSessionRepository(store)
    yield repo
    await store.close()


async def test_resave_does_not_reopen_or_reset_started_at(sqlite_repo) -> None:
    """Saving an already-ended session must preserve started_at and ended_at.

    This test is RED on INSERT OR REPLACE (which deletes and re-inserts,
    wiping ended_at and generating a new started_at).
    """
    repo = sqlite_repo

    await repo.save_session("s", "a", "r", context_payload="v1")

    sessions = await repo.all_sessions()
    s = next(r for r in sessions if r["id"] == "s")
    t0 = s["started_at"]
    assert t0 is not None

    # End the session
    result = await repo.end_session("s")
    assert result == "r"

    sessions = await repo.all_sessions()
    s = next(r for r in sessions if r["id"] == "s")
    e0 = s["ended_at"]
    assert e0 is not None, "ended_at should be set after end_session"

    # Re-save - must NOT reset started_at or ended_at
    await repo.save_session("s", "a", "r", context_payload="v2")

    sessions = await repo.all_sessions()
    s = next(r for r in sessions if r["id"] == "s")

    assert s["started_at"] == t0, (
        f"started_at was reset by re-save: was {t0!r}, now {s['started_at']!r}"
    )
    assert s["ended_at"] is not None, (
        "ended_at was wiped by re-save (INSERT OR REPLACE bug)"
    )
    assert s["ended_at"] == e0, (
        f"ended_at changed: was {e0!r}, now {s['ended_at']!r}"
    )


async def test_end_session_idempotent_sqlite(sqlite_repo) -> None:
    """Second end_session call on same id must still return repo and not re-stamp ended_at."""
    repo = sqlite_repo

    await repo.save_session("s2", "agent", "repo-z")

    result1 = await repo.end_session("s2")
    assert result1 == "repo-z"

    sessions = await repo.all_sessions()
    s = next(r for r in sessions if r["id"] == "s2")
    e0 = s["ended_at"]

    result2 = await repo.end_session("s2")
    assert result2 == "repo-z", f"second end_session should return 'repo-z', got {result2!r}"

    sessions = await repo.all_sessions()
    s = next(r for r in sessions if r["id"] == "s2")
    assert s["ended_at"] == e0, f"ended_at re-stamped: was {e0!r}, now {s['ended_at']!r}"

    # Unknown id
    result3 = await repo.end_session("unknown-id")
    assert result3 is None
