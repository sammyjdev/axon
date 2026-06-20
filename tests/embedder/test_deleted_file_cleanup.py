"""Test D6: files removed from the repo are cleaned from Qdrant and file_index."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


class _DeletionTrackingCache:
    def __init__(self, preloaded_paths: list[str]) -> None:
        # Pre-populate with paths that should be detected as deleted
        self._data = {p: "some_sha1" for p in preloaded_paths}
        self.deleted: list[str] = []

    async def get_all_sha1s(self, ctx: str) -> dict[str, str]:
        return dict(self._data)

    async def set_entry(self, fp, ctx, sha1, chunk_count, *, status="done"):
        self._data[fp] = sha1

    async def delete_entry(self, fp, ctx):
        self.deleted.append(fp)
        self._data.pop(fp, None)

    async def list_entries(self, ctx):
        return list(self._data.items())


@pytest.mark.asyncio
async def test_deleted_file_cleaned_from_qdrant(tmp_path: Path) -> None:
    """After a file is removed, its Qdrant points must be deleted."""
    from axon.embedder.pipeline import index_path

    # Only main.py exists on disk
    py_file = tmp_path / "main.py"
    py_file.write_text("def main(): pass\n", encoding="utf-8")

    # Cache thinks both main.py and deleted.py exist
    deleted_posix = (tmp_path / "deleted.py").as_posix()
    cache = _DeletionTrackingCache([py_file.as_posix(), deleted_posix])

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

    # deleted.py must be removed from file_index
    assert deleted_posix in cache.deleted, (
        "Deleted file must be removed from file_index via delete_entry"
    )
    # delete_by_file must have been called for the deleted path
    delete_calls = [str(c) for c in store.delete_by_file.call_args_list]
    assert any("deleted.py" in c for c in delete_calls), (
        "delete_by_file must be called for deleted.py in Qdrant"
    )
