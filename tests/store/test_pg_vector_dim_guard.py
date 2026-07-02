"""Mixed-dim guard for PgVectorStore (EMB-3).

Unit-level: mocks the asyncpg connection instead of spinning up testcontainers,
since the guard logic (compare existing column dim vs current VECTOR_SIZE) is
the unit under test, not pgvector itself. See emb-3-brief.md test plan.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from axon.store.pg_vector_store import PgVectorStore
from axon.store.vector_common import VECTOR_SIZE


def _store() -> PgVectorStore:
    return PgVectorStore(dsn="postgresql://unused")


@pytest.mark.asyncio
async def test_guard_raises_on_existing_dim_mismatch() -> None:
    """An existing embeddings table at a different dim than VECTOR_SIZE must raise
    a clear, actionable error naming both dims -- never be silently used."""
    store = _store()
    con = AsyncMock()
    con.fetchval = AsyncMock(return_value=384)  # stale pre-bge-m3 column

    with pytest.raises(ValueError, match=r"384") as exc_info:
        await store._check_dimension_guard(con)

    msg = str(exc_info.value)
    assert "1024" in msg
    assert "re-index" in msg.lower() or "reindex" in msg.lower()


@pytest.mark.asyncio
async def test_guard_passes_on_fresh_table() -> None:
    """A fresh/absent table (no existing vector column) proceeds without raising."""
    store = _store()
    con = AsyncMock()
    con.fetchval = AsyncMock(return_value=None)

    await store._check_dimension_guard(con)  # must not raise


@pytest.mark.asyncio
async def test_guard_passes_when_dims_match() -> None:
    """An existing table already at the current VECTOR_SIZE proceeds without raising."""
    store = _store()
    con = AsyncMock()
    con.fetchval = AsyncMock(return_value=VECTOR_SIZE)

    await store._check_dimension_guard(con)  # must not raise
