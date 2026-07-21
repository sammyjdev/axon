from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from axon.embedder.engine import default_embedding_dimension
from axon.embedder.pipeline import index_path


class _FakeFileCache:
    """Minimal FileCache fake tracking ctx + chunk_count per recorded file.

    Mirrors the house idiom in tests/embedder/test_pipeline_excludes.py's
    _MockFileCache, extended to record (ctx, chunk_count) so tests can assert
    per-file ctx routing and a truthy chunk count for nested files.
    """

    def __init__(self) -> None:
        self._sha1: dict[str, str] = {}
        self.entries: dict[str, tuple[str, int]] = {}

    async def get_all_sha1s(self, ctx: str) -> dict[str, str]:
        return dict(self._sha1)

    async def set_entry(self, file_path, ctx, sha1, chunk_count, *, status="done"):
        self._sha1[file_path] = sha1
        if status == "done":
            self.entries[file_path] = (ctx, chunk_count)

    async def delete_entry(self, file_path, ctx):
        self.entries.pop(file_path, None)
        self._sha1.pop(file_path, None)

    async def list_entries(self, ctx):
        return [(fp, c) for fp, (c, _cc) in self.entries.items() if c == ctx]


def _make_engine() -> MagicMock:
    engine = MagicMock()
    engine.embed = MagicMock(
        side_effect=lambda texts: [[0.1] * default_embedding_dimension() for _ in texts]
    )
    return engine


@pytest.mark.asyncio
async def test_index_path_covers_every_vault_subtree_except_work_and_deps(
    tmp_path: Path,
) -> None:
    """Regression for issue #104: index_path (forced_ctx=None, per-file ctx via
    infer_ctx_from_path) must reach every non-excluded, non-work vault file -
    including subtrees the real vault showed at 0% coverage (knowledge/research/,
    projects/, inbox/) and deeply nested files - while an excluded dependency
    dir and the restricted work/ context stay out of the file_index contract.
    """
    vault_root = tmp_path / "vault"

    expected_files = [
        vault_root / "AXON" / "notes.md",
        vault_root / "knowledge" / "index.md",
        vault_root / "knowledge" / "research" / "forge-closed-agentic-loop" / "CONTEXT.md",
        vault_root / "career" / "resume.md",
        vault_root / "projects" / "voxis.md",
        vault_root / "inbox" / "todo.md",
    ]
    for path in expected_files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {path.stem}\n\nBody text for {path.stem}.\n", encoding="utf-8")

    # Excluded dependency dir - must never reach the cache regardless of ctx.
    dep_file = vault_root / "knowledge" / "node_modules" / "pkg" / "readme.md"
    dep_file.parent.mkdir(parents=True)
    dep_file.write_text("# Should never be indexed\n", encoding="utf-8")

    # work/ is restricted (dec-109): present on disk, must stay out of the
    # index when forced_ctx is None (default vault indexing path).
    work_file = vault_root / "work" / "client-notes.md"
    work_file.parent.mkdir(parents=True)
    work_file.write_text("# Restricted\n\nMust not be indexed by default.\n", encoding="utf-8")

    engine = _make_engine()
    store = AsyncMock()
    cache = _FakeFileCache()

    indexed_files, total_chunks = await index_path(
        vault_root,
        engine=engine,
        store=store,
        vault_root=vault_root,
        file_cache=cache,
        forced_ctx=None,
        languages={"markdown"},
    )

    assert indexed_files == len(expected_files)
    assert total_chunks > 0

    recorded_paths = set(cache.entries)
    for path in expected_files:
        assert path.as_posix() in recorded_paths, f"missing from file_index: {path}"

    assert dep_file.as_posix() not in recorded_paths
    assert work_file.as_posix() not in recorded_paths

    # Nested-path case from the real report (>=2 dirs deep under
    # knowledge/research/) must carry a truthy chunk count, proving the fix
    # generalizes past top-level files.
    nested = vault_root / "knowledge" / "research" / "forge-closed-agentic-loop" / "CONTEXT.md"
    nested_ctx, nested_chunk_count = cache.entries[nested.as_posix()]
    assert nested_chunk_count > 0
    assert nested_ctx == "knowledge"
