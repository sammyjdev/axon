"""Tests for MS-6: unified save_code_change error handling + dedupe lock helpers.

Acceptance criteria:
  AC1: pending fallback lives in ONE place (SqliteSessionRepository.save_code_change);
       SessionStore.save_code_change is a thin delegation with NO aiosqlite catch.
  AC2: On the Postgres path a transient error surfaces - not swallowed by dead aiosqlite catch.
  AC3: _is_db_locked / _pending_paths / _warnings_log defined ONCE (shared module).
"""
from __future__ import annotations

import pytest

# ── AC1: SessionStore.save_code_change is a thin delegator ───────────────────


def test_session_store_save_code_change_has_no_aiosqlite_catch() -> None:
    """SessionStore.save_code_change must NOT catch aiosqlite.OperationalError.

    Uses the full module source so indentation is preserved for ast.parse.
    We locate the save_code_change function node and check its ExceptHandlers.
    """
    import ast
    from pathlib import Path

    src = (
        Path(__file__).parent.parent.parent
        / "src/axon/store/session_store.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(src)

    # Find the save_code_change method inside SessionStore
    target_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "save_code_change":
            target_node = node
            break

    assert target_node is not None, "save_code_change not found in session_store.py"

    # Walk all ExceptHandler nodes inside this method - none should reference aiosqlite
    for node in ast.walk(target_node):
        if isinstance(node, ast.ExceptHandler):
            if node.type is not None:
                type_src = ast.unparse(node.type)
                assert "aiosqlite" not in type_src, (
                    "SessionStore.save_code_change must not catch aiosqlite errors; "
                    f"found: except {type_src}"
                )


@pytest.mark.asyncio
async def test_session_store_delegates_to_repo_save_code_change(
    tmp_path, monkeypatch
) -> None:
    """SessionStore.save_code_change delegates to repo.save_code_change (not inner)."""
    monkeypatch.setenv("AXON_SESSIONS_BACKEND", "sqlite")
    from unittest.mock import AsyncMock

    from axon.store.session_store import CodeChange, SessionStore

    store = SessionStore(db_path=tmp_path / "axon.db")
    await store.init()

    # Grab the repo instance and patch save_code_change on it
    repo = await store._sessions()
    repo.save_code_change = AsyncMock()  # type: ignore[method-assign]

    change = CodeChange(
        commit_hash="abc123",
        file_path="src/Engine.java",
        diff_summary="test",
    )
    await store.save_code_change(change)

    repo.save_code_change.assert_awaited_once_with(change)
    await store.close()


# ── AC2: Postgres path - transient error surfaces ────────────────────────────


@pytest.mark.asyncio
async def test_postgres_path_transient_error_surfaces(tmp_path, monkeypatch) -> None:
    """On the Postgres path a non-aiosqlite error propagates out of save_code_change.

    This is the key Postgres-path contract: errors are NOT silently swallowed
    by a dead aiosqlite.OperationalError catch.  We use a plain RuntimeError to
    simulate a transient PG error.
    """
    monkeypatch.setenv("AXON_SESSIONS_BACKEND", "postgres")
    from unittest.mock import AsyncMock

    from axon.store.session_store import CodeChange, SessionStore

    store = SessionStore(db_path=tmp_path / "axon.db")
    await store.init()

    # Inject a fake Postgres repo whose save_code_change raises
    class FakePgRepo:
        async def ensure_schema(self) -> None:
            pass

        async def save_code_change(self, change) -> None:
            raise RuntimeError("transient PG error")

    store._session_repo = FakePgRepo()

    change = CodeChange(
        commit_hash="pg1",
        file_path="src/Service.py",
        diff_summary="postgres write",
    )
    with pytest.raises(RuntimeError, match="transient PG error"):
        await store.save_code_change(change)

    await store.close()


# ── AC3: helpers defined once in shared module ───────────────────────────────


def test_lock_helpers_defined_in_shared_module_only() -> None:
    """_is_db_locked / _pending_paths / _warnings_log must live in ONE shared module.

    Both session_store and session_repository should IMPORT them from the
    shared module rather than re-defining them.
    """
    # The shared module must export all three helpers
    from axon.store import sqlite_helpers  # must exist

    assert callable(sqlite_helpers._is_db_locked), "_is_db_locked missing from sqlite_helpers"
    assert callable(sqlite_helpers._pending_paths), "_pending_paths missing from sqlite_helpers"
    assert callable(sqlite_helpers._warnings_log), "_warnings_log missing from sqlite_helpers"


def test_session_store_does_not_redefine_lock_helpers() -> None:
    """session_store.py must not define its own _is_db_locked/_pending_paths/_warnings_log."""
    import ast
    from pathlib import Path

    src = (
        Path(__file__).parent.parent.parent
        / "src/axon/store/session_store.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(src)

    # Top-level function defs only (not class methods)
    top_level_funcs = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and any(
            isinstance(parent, ast.Module)
            for parent in ast.walk(tree)
            if isinstance(parent, ast.Module)
            and any(child is node for child in ast.iter_child_nodes(parent))
        )
    }

    for name in ("_is_db_locked", "_pending_paths", "_warnings_log"):
        assert name not in top_level_funcs, (
            f"session_store.py redefines {name!r}; "
            "it must import from axon.store.sqlite_helpers instead"
        )


def test_session_repository_does_not_redefine_lock_helpers() -> None:
    """session_repository.py must not define its own _is_db_locked/_pending_paths/_warnings_log."""
    import ast
    from pathlib import Path

    src = (
        Path(__file__).parent.parent.parent
        / "src/axon/store/session_repository.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(src)

    top_level_funcs = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and any(
            isinstance(parent, ast.Module)
            for parent in ast.walk(tree)
            if isinstance(parent, ast.Module)
            and any(child is node for child in ast.iter_child_nodes(parent))
        )
    }

    for name in ("_is_db_locked", "_pending_paths", "_warnings_log"):
        assert name not in top_level_funcs, (
            f"session_repository.py redefines {name!r}; "
            "it must import from axon.store.sqlite_helpers instead"
        )


# ── AC1 (behavioral): SQLite locked path still writes a pending file ─────────


@pytest.mark.asyncio
async def test_sqlite_locked_pending_fallback_still_works(
    tmp_path, monkeypatch
) -> None:
    """When SQLite is locked, save_code_change writes a pending file + warning.

    This verifies the behavioral contract is preserved after the refactor.
    """
    monkeypatch.setenv("AXON_SESSIONS_BACKEND", "sqlite")
    monkeypatch.setenv("AXON_DATA_ROOT", str(tmp_path / "data"))

    import aiosqlite

    from axon.store.session_store import CodeChange, SessionStore

    store = SessionStore(db_path=tmp_path / "axon.db")
    await store.init()

    # Patch save_code_change_inner on the SQLite repo to simulate a lock error
    repo = await store._sessions()

    async def locked_inner(change) -> None:
        raise aiosqlite.OperationalError("database is locked")

    repo.save_code_change_inner = locked_inner  # type: ignore[method-assign]

    change = CodeChange(
        commit_hash="deadbeef",
        file_path="src/Locked.java",
        diff_summary="locked write test",
    )
    # Should NOT raise - falls back to pending
    await store.save_code_change(change)

    pending_dir = tmp_path / "data" / "pending"
    pending_files = list(pending_dir.glob("*.json"))
    assert len(pending_files) == 1, f"Expected 1 pending file, got {pending_files}"

    import json

    payload = json.loads(pending_files[0].read_text())
    assert payload["kind"] == "code_change"
    assert payload["commit_hash"] == "deadbeef"

    await store.close()
