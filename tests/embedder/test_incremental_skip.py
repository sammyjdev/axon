"""Test that unchanged files are skipped on the second index run."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from axon.embedder.engine import default_embedding_dimension


class _MockFileCache:
    """Simulates a cache that already has every file as done with the correct sha1."""

    def __init__(self, preloaded: dict[str, str]) -> None:
        self._data = preloaded

    async def get_all_sha1s(self, ctx: str) -> dict[str, str]:
        return dict(self._data)

    async def set_entry(self, file_path, ctx, sha1, chunk_count, *, status="done"):
        self._data[file_path] = sha1

    async def delete_entry(self, file_path, ctx):
        self._data.pop(file_path, None)

    async def list_entries(self, ctx):
        return list(self._data.items())


@pytest.mark.asyncio
async def test_unchanged_file_skipped(tmp_path: Path) -> None:
    """Engine.embed must not be called for a file whose sha1 is cached."""
    from axon.embedder.pipeline import index_path
    from axon.store.file_cache import sha1_of_source

    py_file = tmp_path / "hello.py"
    py_file.write_text("def hello(): return 1\n", encoding="utf-8")
    source = py_file.read_text(encoding="utf-8")
    cached_sha1 = sha1_of_source(source)
    fp_posix = py_file.as_posix()

    cache = _MockFileCache({fp_posix: cached_sha1})

    engine = MagicMock()
    engine.embed = MagicMock(return_value=[])

    store = AsyncMock()
    store.upsert_batch = AsyncMock()
    store.delete_by_file = AsyncMock()

    indexed, total = await index_path(
        tmp_path,
        engine=engine,
        store=store,
        vault_root=tmp_path,
        file_cache=cache,
    )

    engine.embed.assert_not_called()
    assert indexed == 0
    assert total == 0


@pytest.mark.asyncio
async def test_changed_file_reindexed(tmp_path: Path) -> None:
    """A file with a different sha1 must pass through embed."""
    from axon.embedder.pipeline import index_path

    py_file = tmp_path / "hello.py"
    py_file.write_text("def hello(): return 1\n", encoding="utf-8")

    # Cache has a WRONG (stale) sha1 - simulates a changed file
    cache = _MockFileCache({py_file.as_posix(): "stale_sha1_that_differs"})

    engine = MagicMock()
    engine.embed = MagicMock(return_value=[[0.1] * default_embedding_dimension()])

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

    engine.embed.assert_called()
    store.delete_by_file.assert_called()
