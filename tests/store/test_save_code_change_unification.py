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
