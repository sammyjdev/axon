"""Structural typing tests for SessionRepository Protocol (issue #34 / MS-8).

Verifies:
1. Both SqliteSessionRepository and PostgresSessionRepository are
   isinstance-compatible with the @runtime_checkable SessionRepository Protocol.
2. All Protocol method annotations are concrete types (not Any / bare params)
   via typing.get_type_hints.
3. Both impls produce the same model types for each round-trip method
   (SQLite only - no real DB required for structural checks).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import get_type_hints

import pytest

from axon.store.pg_session_repository import PostgresSessionRepository
from axon.store.session_repository import SessionRepository, SqliteSessionRepository
from axon.store.session_store import CodeChange, SessionMemory, SessionNote

# ── isinstance checks (runtime_checkable) ────────────────────────────────────

def test_sqlite_repo_is_session_repository():
    """SqliteSessionRepository must satisfy the @runtime_checkable Protocol."""
    store_stub = object()  # isinstance only checks method presence, not the arg
    repo = SqliteSessionRepository(store_stub)
    assert isinstance(repo, SessionRepository)


def test_postgres_repo_is_session_repository():
    """PostgresSessionRepository must satisfy the @runtime_checkable Protocol."""
    repo = PostgresSessionRepository(dsn="postgresql://localhost/dummy")
    assert isinstance(repo, SessionRepository)


# ── Annotation completeness via get_type_hints ───────────────────────────────

_PROTO_METHODS = [
    "save_session_memory",
    "get_session_memories",
    "save_note",
    "get_notes",
    "save_code_change_inner",
    "save_code_change",
    "get_recent_changes",
    "save_session",
    "end_session",
    "all_memories",
    "all_notes",
    "all_code_changes",
    "all_sessions",
]

# Map method name -> parameter name(s) that were previously bare (implicit Any)
_TYPED_PARAMS: dict[str, dict[str, type]] = {
    "save_session_memory": {"mem": SessionMemory},
    "save_note": {"note": SessionNote},
    "save_code_change_inner": {"change": CodeChange},
    "save_code_change": {"change": CodeChange},
}


@pytest.mark.parametrize("method_name,param,expected_type", [
    (method, param, expected)
    for method, params in _TYPED_PARAMS.items()
    for param, expected in params.items()
])
def test_protocol_param_is_typed(method_name: str, param: str, expected_type: type):
    """Each formerly-bare Protocol param must carry a concrete annotation."""
    hints = get_type_hints(getattr(SessionRepository, method_name))
    assert param in hints, (
        f"SessionRepository.{method_name} is missing annotation for '{param}'"
    )
    assert hints[param] is expected_type, (
        f"SessionRepository.{method_name}.{param}: expected {expected_type}, "
        f"got {hints[param]}"
    )


@pytest.mark.parametrize("method_name,param,expected_type", [
    (method, param, expected)
    for method, params in _TYPED_PARAMS.items()
    for param, expected in params.items()
])
def test_sqlite_impl_param_is_typed(method_name: str, param: str, expected_type: type):
    """SqliteSessionRepository method params must match the Protocol's types."""
    hints = get_type_hints(getattr(SqliteSessionRepository, method_name))
    assert param in hints, (
        f"SqliteSessionRepository.{method_name} is missing annotation for '{param}'"
    )
    assert hints[param] is expected_type, (
        f"SqliteSessionRepository.{method_name}.{param}: expected {expected_type}, "
        f"got {hints[param]}"
    )


@pytest.mark.parametrize("method_name,param,expected_type", [
    (method, param, expected)
    for method, params in _TYPED_PARAMS.items()
    for param, expected in params.items()
])
def test_postgres_impl_param_is_typed(method_name: str, param: str, expected_type: type):
    """PostgresSessionRepository method params must match the Protocol's types."""
    hints = get_type_hints(getattr(PostgresSessionRepository, method_name))
    assert param in hints, (
        f"PostgresSessionRepository.{method_name} is missing annotation for '{param}'"
    )
    assert hints[param] is expected_type, (
        f"PostgresSessionRepository.{method_name}.{param}: expected {expected_type}, "
        f"got {hints[param]}"
    )


# ── Round-trip structural test (SQLite in-memory) ────────────────────────────

@pytest.fixture
async def sqlite_repo(tmp_path):
    from axon.store.session_store import SessionStore

    store = SessionStore(db_path=tmp_path / "axon.db")
    await store.init()
    repo = await store._sessions()
    yield repo
    await store.close()


async def test_sqlite_round_trip_session_memory(sqlite_repo):
    mem = SessionMemory(project="proj", summary="s", raw_turns=2)
    row_id = await sqlite_repo.save_session_memory(mem)
    assert isinstance(row_id, int) and row_id >= 1
    results = await sqlite_repo.get_session_memories("proj", limit=1)
    assert len(results) == 1
    assert isinstance(results[0], SessionMemory)
    assert results[0].summary == "s"
    assert results[0].raw_turns == 2


async def test_sqlite_round_trip_session_note(sqlite_repo):
    note = SessionNote(project="proj", body="hello")
    row_id = await sqlite_repo.save_note(note)
    assert isinstance(row_id, int) and row_id >= 1
    results = await sqlite_repo.get_notes("proj", limit=1)
    assert len(results) == 1
    assert isinstance(results[0], SessionNote)
    assert results[0].body == "hello"


async def test_sqlite_round_trip_code_change(sqlite_repo):
    change = CodeChange(
        commit_hash="deadbeef",
        file_path="src/foo.py",
        diff_summary="added bar",
        why="tests",
        changed_at=datetime.now(UTC),
    )
    await sqlite_repo.save_code_change(change)
    results = await sqlite_repo.get_recent_changes("src/foo.py", limit=1)
    assert len(results) == 1
    assert isinstance(results[0], CodeChange)
    assert results[0].commit_hash == "deadbeef"
    assert results[0].diff_summary == "added bar"
