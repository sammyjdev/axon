"""Regression (whole-branch review C1): indexing a SINGLE file must not delete
sibling files' points from the ctx. The D6 orphan reconcile previously assumed
target == ctx root, so a single-file index (watch / per-howto / expansion
publish) wiped every other file in the ctx."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock


class _MockFileCache:
    def __init__(self, preloaded: dict[str, str]) -> None:
        self._data = dict(preloaded)

    async def get_all_sha1s(self, ctx: str) -> dict[str, str]:
        return dict(self._data)

    async def set_entry(self, fp, ctx, sha1, cc, *, status="done"):  # noqa: ANN001
        if status == "done":
            self._data[fp] = sha1

    async def delete_entry(self, fp, ctx):  # noqa: ANN001
        self._data.pop(fp, None)

    async def list_entries(self, ctx):  # noqa: ANN001
        return list(self._data.items())


async def test_single_file_index_does_not_wipe_siblings(tmp_path: Path) -> None:
    from axon.embedder.pipeline import index_path
    from axon.store.file_cache import sha1_of_source

    file_a = tmp_path / "a.py"
    file_b = tmp_path / "b.py"
    file_a.write_text("def a():\n    return 1\n", encoding="utf-8")
    file_b.write_text("def b():\n    return 2\n", encoding="utf-8")
    a_posix = file_a.as_posix()
    b_posix = file_b.as_posix()

    # Both already indexed; a has its CURRENT sha1 (so it is skipped this run).
    cache = _MockFileCache(
        {a_posix: sha1_of_source(file_a.read_text(encoding="utf-8")), b_posix: "stale-b"}
    )
    engine = MagicMock()
    engine.embed = MagicMock(return_value=[])
    store = AsyncMock()

    # Index ONLY file_a (single-file target).
    await index_path(file_a, engine=engine, store=store, vault_root=tmp_path, file_cache=cache)

    deleted_paths = [
        str(arg) for call in store.delete_by_file.call_args_list for arg in call.args
    ]
    assert not any(b_posix in p for p in deleted_paths), (
        f"sibling b.py was wrongly deleted on a single-file index: {deleted_paths}"
    )
    assert b_posix in cache._data, "b.py cache entry must survive a single-file index of a.py"


async def test_directory_index_still_reconciles_removed_file(tmp_path: Path) -> None:
    """The fix must not disable real reconcile: a full-directory index still
    deletes a cached file that no longer exists on disk."""
    from axon.embedder.pipeline import index_path
    from axon.store.file_cache import sha1_of_source

    file_a = tmp_path / "a.py"
    file_a.write_text("def a():\n    return 1\n", encoding="utf-8")
    a_posix = file_a.as_posix()
    gone_posix = (tmp_path / "gone.py").as_posix()  # cached but not on disk

    cache = _MockFileCache(
        {a_posix: sha1_of_source(file_a.read_text(encoding="utf-8")), gone_posix: "old"}
    )
    engine = MagicMock()
    engine.embed = MagicMock(return_value=[])
    store = AsyncMock()

    # Index the whole directory.
    await index_path(tmp_path, engine=engine, store=store, vault_root=tmp_path, file_cache=cache)

    deleted_paths = [
        str(arg) for call in store.delete_by_file.call_args_list for arg in call.args
    ]
    assert any(gone_posix in p for p in deleted_paths), "removed file must be reconciled away"
    assert gone_posix not in cache._data
