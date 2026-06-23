"""Regression tests for coroutine-safe lazy pool and repository init (issue #29 / MS-5).

Each _ensure_pool must guard init with a double-checked asyncio.Lock so that
concurrent first-callers cannot orphan a pool or call ensure_schema twice.
SessionStore._sessions/_graph/_decisions must use a dedicated init lock
(not self._lock, which serializes SQLite I/O) for the same reason.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helper: count how many times create_pool is called under concurrency
# ---------------------------------------------------------------------------

async def _count_create_pool_calls(repo_class, dsn: str) -> int:
    """Instantiate repo_class, then fire 20 concurrent _ensure_pool calls.

    Returns the number of times asyncpg.create_pool was actually invoked.
    """
    call_count = 0
    original_pool_sentinel = object()

    async def fake_create_pool(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        # Small yield so other coroutines can interleave before the assignment
        await asyncio.sleep(0)
        pool = AsyncMock()
        pool.close = AsyncMock()
        return pool

    repo = repo_class(dsn=dsn)

    with patch("asyncpg.create_pool", side_effect=fake_create_pool):
        # 20 concurrent first-callers -- all see pool is None initially
        await asyncio.gather(*[repo._ensure_pool() for _ in range(20)])

    return call_count


# ---------------------------------------------------------------------------
# Tests: _ensure_pool is idempotent under concurrency for each repository
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pg_session_repository_ensure_pool_once():
    from axon.store.pg_session_repository import PostgresSessionRepository

    count = await _count_create_pool_calls(PostgresSessionRepository, "postgresql://fake/db")
    assert count == 1, f"Expected 1 pool creation, got {count}"


@pytest.mark.asyncio
async def test_pg_graph_repository_ensure_pool_once():
    from axon.store.pg_graph_repository import PostgresGraphRepository

    count = await _count_create_pool_calls(PostgresGraphRepository, "postgresql://fake/db")
    assert count == 1, f"Expected 1 pool creation, got {count}"


@pytest.mark.asyncio
async def test_pg_decision_repository_ensure_pool_once():
    from axon.store.pg_decision_repository import PostgresDecisionRepository

    count = await _count_create_pool_calls(PostgresDecisionRepository, "postgresql://fake/db")
    assert count == 1, f"Expected 1 pool creation, got {count}"


# ---------------------------------------------------------------------------
# Test: SessionStore lazy repo init is idempotent under concurrency
# For each of _sessions/_graph/_decisions we count constructor calls.
# ---------------------------------------------------------------------------

async def _count_repo_constructor_calls(method_name: str, tmp_path) -> int:
    """Fire 20 concurrent calls to store.<method_name>() and count constructions."""
    from axon.store.session_store import SessionStore

    call_count = 0

    # Determine which env var and which module/class to patch
    backend_env = {
        "_sessions": ("AXON_SESSIONS_BACKEND", "postgres",
                      "axon.store.pg_session_repository.PostgresSessionRepository"),
        "_graph": ("AXON_GRAPH_BACKEND", "postgres",
                   "axon.store.pg_graph_repository.PostgresGraphRepository"),
        "_decisions": ("AXON_DECISIONS_BACKEND", "postgres",
                       "axon.store.pg_decision_repository.PostgresDecisionRepository"),
    }

    env_var, backend_val, patch_target = backend_env[method_name]

    class FakeRepo:
        def __init__(self, dsn: str) -> None:
            nonlocal call_count
            call_count += 1

        async def ensure_schema(self) -> None:
            await asyncio.sleep(0)  # yield to allow interleaving

    import os
    old_val = os.environ.get(env_var)
    os.environ[env_var] = backend_val
    try:
        store = SessionStore(db_path=tmp_path / "axon.db")
        await store.init()

        with patch(patch_target, FakeRepo):
            method = getattr(store, method_name)
            await asyncio.gather(*[method() for _ in range(20)])
    finally:
        if old_val is None:
            del os.environ[env_var]
        else:
            os.environ[env_var] = old_val
        await store.close()

    return call_count


@pytest.mark.asyncio
async def test_session_store_sessions_repo_init_once(tmp_path):
    count = await _count_repo_constructor_calls("_sessions", tmp_path)
    assert count == 1, f"Expected 1 _sessions repo construction, got {count}"


@pytest.mark.asyncio
async def test_session_store_graph_repo_init_once(tmp_path):
    count = await _count_repo_constructor_calls("_graph", tmp_path)
    assert count == 1, f"Expected 1 _graph repo construction, got {count}"


@pytest.mark.asyncio
async def test_session_store_decisions_repo_init_once(tmp_path):
    count = await _count_repo_constructor_calls("_decisions", tmp_path)
    assert count == 1, f"Expected 1 _decisions repo construction, got {count}"


# ---------------------------------------------------------------------------
# Test: SessionStore uses a DEDICATED init lock (not self._lock)
# ---------------------------------------------------------------------------

def test_session_store_has_dedicated_repo_init_lock():
    """SessionStore.__init__ must create self._repo_init_lock separate from self._lock."""
    from axon.store.session_store import SessionStore
    import asyncio

    store = SessionStore()
    assert hasattr(store, "_repo_init_lock"), "SessionStore must have _repo_init_lock"
    assert isinstance(store._repo_init_lock, asyncio.Lock)
    assert store._repo_init_lock is not store._lock, (
        "_repo_init_lock must be a different lock from _lock"
    )


def test_pg_session_repository_has_init_lock():
    from axon.store.pg_session_repository import PostgresSessionRepository
    import asyncio

    repo = PostgresSessionRepository(dsn="postgresql://fake/db")
    assert hasattr(repo, "_pool_lock"), "PostgresSessionRepository must have _pool_lock"
    assert isinstance(repo._pool_lock, asyncio.Lock)


def test_pg_graph_repository_has_init_lock():
    from axon.store.pg_graph_repository import PostgresGraphRepository
    import asyncio

    repo = PostgresGraphRepository(dsn="postgresql://fake/db")
    assert hasattr(repo, "_pool_lock"), "PostgresGraphRepository must have _pool_lock"
    assert isinstance(repo._pool_lock, asyncio.Lock)


def test_pg_decision_repository_has_init_lock():
    from axon.store.pg_decision_repository import PostgresDecisionRepository
    import asyncio

    repo = PostgresDecisionRepository(dsn="postgresql://fake/db")
    assert hasattr(repo, "_pool_lock"), "PostgresDecisionRepository must have _pool_lock"
    assert isinstance(repo._pool_lock, asyncio.Lock)
