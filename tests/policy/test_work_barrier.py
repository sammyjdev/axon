from __future__ import annotations

from pathlib import Path

import pytest

from axon.embedder.chunker import Chunk
from axon.embedder.pipeline import index_path
from axon.store.collections import get_search_collections


class _NullCache:
    """Minimal no-op FileCache for policy tests that do not need caching behaviour."""

    async def get_all_sha1s(self, ctx: str, *, chunker_version=None) -> dict[str, str]:
        return {}

    async def set_entry(
        self, fp, ctx, sha1, cc, *, status="done", chunker_version=None
    ) -> None:
        pass

    async def delete_entry(self, fp, ctx) -> None:
        pass

    async def list_entries(self, ctx) -> list:
        return []


class FakeEngine:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text))] for text in texts]


class FakeStore:
    def __init__(self) -> None:
        self.batches: list[list[object]] = []

    async def upsert_batch(self, chunks: list[object]) -> None:
        self.batches.append(list(chunks))

    async def delete_by_file(self, ctx: str, file_path: str) -> None:
        pass


def test_search_collections_hide_work_without_explicit_context() -> None:
    """work is the only hard boundary; the other four never leak it (dec-109)."""
    assert get_search_collections(None) == ["personal", "career", "knowledge", "saas"]
    assert get_search_collections("work") == ["work"]
    for ctx in ("knowledge", "personal", "saas", "career", "", "nonsense"):
        assert "work" not in get_search_collections(ctx)


def test_non_protected_ctx_widens_instead_of_partitioning() -> None:
    """dec-131: asking with ctx=knowledge must still reach code indexed as
    personal. Measured 2026-08-29: 'repo_identity git-common-dir' returned 0
    hits under knowledge and 5 under personal, because ctx filtered the search.
    """
    for ctx in ("knowledge", "personal", "saas", "career"):
        assert get_search_collections(ctx) == ["personal", "career", "knowledge", "saas"]


@pytest.mark.asyncio
async def test_index_path_skips_work_tree_without_explicit_context(
    monkeypatch, tmp_path: Path
) -> None:
    vault_root = tmp_path / "vault"
    knowledge_file = vault_root / "knowledge" / "notes.md"
    work_file = vault_root / "work" / "secret.md"
    knowledge_file.parent.mkdir(parents=True, exist_ok=True)
    work_file.parent.mkdir(parents=True, exist_ok=True)
    knowledge_file.write_text("# Knowledge\nsafe note\n", encoding="utf-8")
    work_file.write_text("# Work\nconfidential\n", encoding="utf-8")

    def fake_chunk_source(source: str, language: str, file_path: str) -> list[Chunk]:
        return [
            Chunk(
                symbol=Path(file_path).stem,
                chunk_type="class",
                start_line=1,
                end_line=1,
                content=source,
                file_path=file_path,
                language=language,
            )
        ]

    monkeypatch.setattr("axon.embedder.pipeline.chunk_source", fake_chunk_source)

    store = FakeStore()
    indexed_files, total_chunks = await index_path(
        vault_root,
        engine=FakeEngine(),
        store=store,
        vault_root=vault_root,
        file_cache=_NullCache(),
    )

    # FIX 3: index_path stores fp_posix, so compare with as_posix()
    indexed_paths = {chunk.file_path for batch in store.batches for chunk in batch}

    assert indexed_files == 1
    assert total_chunks == 1
    assert knowledge_file.as_posix() in indexed_paths
    assert work_file.as_posix() not in indexed_paths


@pytest.mark.asyncio
async def test_index_path_allows_work_when_context_is_explicit(monkeypatch, tmp_path: Path) -> None:
    vault_root = tmp_path / "vault"
    work_file = vault_root / "work" / "secret.md"
    work_file.parent.mkdir(parents=True, exist_ok=True)
    work_file.write_text("# Work\nconfidential\n", encoding="utf-8")

    def fake_chunk_source(source: str, language: str, file_path: str) -> list[Chunk]:
        return [
            Chunk(
                symbol=Path(file_path).stem,
                chunk_type="class",
                start_line=1,
                end_line=1,
                content=source,
                file_path=file_path,
                language=language,
            )
        ]

    monkeypatch.setattr("axon.embedder.pipeline.chunk_source", fake_chunk_source)

    store = FakeStore()
    indexed_files, total_chunks = await index_path(
        work_file,
        engine=FakeEngine(),
        store=store,
        vault_root=vault_root,
        forced_ctx="work",
        file_cache=_NullCache(),
    )

    indexed_chunks = [chunk for batch in store.batches for chunk in batch]

    assert indexed_files == 1
    assert total_chunks == 1
    assert len(indexed_chunks) == 1
    assert indexed_chunks[0].ctx == "work"
    # FIX 3: index_path stores fp_posix, so compare with as_posix()
    assert indexed_chunks[0].file_path == work_file.as_posix()


@pytest.mark.asyncio
async def test_index_path_reprocesses_a_file_when_the_chunker_version_moves(
    monkeypatch, tmp_path: Path
) -> None:
    """The end-to-end behaviour dec-132 needed and did not have.

    The cache keys on the file's sha1, so a chunker improvement was invisible to
    every unchanged file: after dec-132 landed, a full index-dev run reprocessed
    75 of 575 files and only a manual DELETE on file_index recovered the rest.
    """
    from axon.embedder import pipeline

    src = tmp_path / "personal" / "m.py"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("class C:\n    def a(self):\n        pass\n", encoding="utf-8")

    class VersionedCache(_NullCache):
        def __init__(self) -> None:
            self.rows: dict[str, tuple[str, str | None]] = {}

        async def get_all_sha1s(self, ctx, *, chunker_version=None):
            return {
                fp: sha
                for fp, (sha, ver) in self.rows.items()
                if chunker_version is None or ver == chunker_version
            }

        async def set_entry(self, fp, ctx, sha1, cc, *, status="done",
                            chunker_version=None) -> None:
            self.rows[str(fp)] = (sha1, chunker_version)

    cache = VersionedCache()
    store = FakeStore()

    async def run() -> int:
        before = len(store.batches)
        await index_path(
            tmp_path,
            engine=FakeEngine(),
            store=store,
            vault_root=tmp_path,
            file_cache=cache,
            languages={"python"},
        )
        return len(store.batches) - before

    monkeypatch.setattr(pipeline, "CHUNKER_VERSION", "v-old")
    assert await run() > 0, "first pass must index the file"
    assert await run() == 0, "unchanged file and version must be skipped"

    # The source is untouched; only the chunker moved.
    monkeypatch.setattr(pipeline, "CHUNKER_VERSION", "v-new")
    assert await run() > 0, "a new chunker version must reindex the untouched file"
