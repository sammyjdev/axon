"""Test D2: pending sentinel survives a crash; re-index picks it up."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


class _SentinelTrackingCache:
    """Tracks set_entry calls to verify pending/done ordering."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._data: dict[str, str] = {}

    async def get_all_sha1s(self, ctx: str) -> dict[str, str]:
        # Simulate crash: return empty (pending rows excluded)
        return {}

    async def set_entry(self, fp, ctx, sha1, chunk_count, *, status="done"):
        self.calls.append({"fp": fp, "status": status, "sha1": sha1})
        if status == "done":
            self._data[fp] = sha1

    async def delete_entry(self, fp, ctx):
        self._data.pop(fp, None)

    async def list_entries(self, ctx):
        return []


@pytest.mark.asyncio
async def test_pending_written_before_flush(tmp_path: Path) -> None:
    """status='pending' must appear before status='done' in the call sequence."""
    from axon.embedder.pipeline import index_path

    py_file = tmp_path / "foo.py"
    py_file.write_text("def foo(): pass\n", encoding="utf-8")

    cache = _SentinelTrackingCache()
    engine = MagicMock()
    engine.embed = MagicMock(return_value=[[0.1] * 768])
    store = AsyncMock()
    store.upsert_batch = AsyncMock()
    store.delete_by_file = AsyncMock()

    await index_path(
        tmp_path,
        engine=engine,
        store=store,
        vault_root=tmp_path,
        file_cache=cache,
    )

    statuses = [c["status"] for c in cache.calls]
    assert "pending" in statuses, "set_entry(pending) must be called"
    assert "done" in statuses, "set_entry(done) must be called"
    pending_idx = next(i for i, c in enumerate(cache.calls) if c["status"] == "pending")
    done_idx = next(i for i, c in enumerate(cache.calls) if c["status"] == "done")
    assert pending_idx < done_idx, (
        "pending must be written BEFORE done (crash-safety invariant D2)"
    )
