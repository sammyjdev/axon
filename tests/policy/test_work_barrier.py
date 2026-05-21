from __future__ import annotations

from pathlib import Path

import pytest

from axon.embedder.chunker import Chunk
from axon.embedder.pipeline import index_path
from axon.store.collections import get_search_collections


class FakeEngine:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text))] for text in texts]


class FakeStore:
    def __init__(self) -> None:
        self.batches: list[list[object]] = []

    async def upsert_batch(self, chunks: list[object]) -> None:
        self.batches.append(list(chunks))


def test_search_collections_hide_work_without_explicit_context() -> None:
    assert get_search_collections(None) == ["personal", "career", "knowledge", "saas"]
    assert get_search_collections("knowledge") == ["knowledge"]
    assert get_search_collections("personal") == ["personal"]
    assert get_search_collections("saas") == ["saas"]
    assert get_search_collections("work") == ["work"]


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
    )

    indexed_paths = {chunk.file_path for batch in store.batches for chunk in batch}

    assert indexed_files == 1
    assert total_chunks == 1
    assert str(knowledge_file) in indexed_paths
    assert str(work_file) not in indexed_paths


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
    )

    indexed_chunks = [chunk for batch in store.batches for chunk in batch]

    assert indexed_files == 1
    assert total_chunks == 1
    assert len(indexed_chunks) == 1
    assert indexed_chunks[0].ctx == "work"
    assert indexed_chunks[0].file_path == str(work_file)
