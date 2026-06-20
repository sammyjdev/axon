from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from axon.embedder.chunker import Chunk


class _NullCache:
    """Minimal no-op FileCache for tests that do not need caching behaviour."""

    async def get_all_sha1s(self, ctx: str) -> dict[str, str]:
        return {}

    async def set_entry(self, fp, ctx, sha1, cc, *, status="done") -> None:
        pass

    async def delete_entry(self, fp, ctx) -> None:
        pass

    async def list_entries(self, ctx) -> list:
        return []


def _make_chunk(content: str, symbol: str = "f", file_path: str = "test.py") -> Chunk:
    return Chunk(
        symbol=symbol,
        chunk_type="function",
        start_line=1,
        end_line=content.count("\n") + 1,
        content=content,
        file_path=file_path,
        language="python",
    )


@pytest.mark.asyncio
async def test_index_path_uses_bounded_batching() -> None:
    """index_path must call _make_token_bounded_batches, not embed all chunks at once."""
    import axon.embedder.pipeline as pipeline_mod

    chunks = [_make_chunk("x " * 200, f"func_{i}") for i in range(5)]

    call_log: list[list[str]] = []

    def tracking_embed(texts: list[str]) -> list[list[float]]:
        call_log.append(texts)
        return [[0.0] * 768 for _ in texts]

    mock_engine = MagicMock()
    mock_engine.embed.side_effect = tracking_embed

    mock_store = MagicMock()
    mock_store.upsert_batch = AsyncMock()
    mock_store.delete_by_file = AsyncMock()

    with (
        patch.object(pipeline_mod, "iter_supported_files", return_value=[Path("test.py")]),
        patch.object(pipeline_mod, "chunk_source", return_value=chunks),
        patch.object(Path, "read_text", return_value="def func(): pass\n"),
        patch.object(
            pipeline_mod,
            "_make_token_bounded_batches",
            wraps=pipeline_mod._make_token_bounded_batches,
        ) as spy_batches,
    ):
        await pipeline_mod.index_path(
            target=Path("repo"),
            engine=mock_engine,
            store=mock_store,
            vault_root=Path("vault"),
            file_cache=_NullCache(),
        )

    assert spy_batches.called, "_make_token_bounded_batches must be called inside index_path"


@pytest.mark.asyncio
async def test_index_path_embeds_all_chunks_across_batches() -> None:
    """All chunk texts must be embedded even when split across multiple batches."""
    import axon.embedder.pipeline as pipeline_mod

    chunks = [_make_chunk(f"content of function {i}", f"f{i}") for i in range(3)]
    embedded_texts: list[str] = []

    def tracking_embed(texts: list[str]) -> list[list[float]]:
        embedded_texts.extend(texts)
        return [[float(j) for j in range(768)] for _ in texts]

    mock_engine = MagicMock()
    mock_engine.embed.side_effect = tracking_embed

    mock_store = MagicMock()
    mock_store.upsert_batch = AsyncMock()
    mock_store.delete_by_file = AsyncMock()

    with (
        patch.object(pipeline_mod, "iter_supported_files", return_value=[Path("test.py")]),
        patch.object(pipeline_mod, "chunk_source", return_value=chunks),
        patch.object(Path, "read_text", return_value="def f(): pass\n"),
    ):
        indexed_files, total_chunks = await pipeline_mod.index_path(
            target=Path("repo"),
            engine=mock_engine,
            store=mock_store,
            vault_root=Path("vault"),
            file_cache=_NullCache(),
        )

    assert total_chunks == 3, f"Expected 3 total chunks, got {total_chunks}"
    assert len(embedded_texts) == 3, f"Expected 3 embedded texts, got {len(embedded_texts)}"


@pytest.mark.asyncio
async def test_index_path_embed_called_multiple_times_when_batched() -> None:
    """engine.embed must be called more than once when chunks exceed token budget."""
    import axon.embedder.pipeline as pipeline_mod

    # Each chunk has ~400 chars * 0.35 = ~140 tokens. With AXON_MAX_BATCH_TOKENS=200,
    # each batch fits at most 1 chunk, so embed is called once per chunk.
    big_content = "word " * 80  # ~400 chars -> ~140 estimated tokens
    chunks = [_make_chunk(big_content, f"fn_{i}") for i in range(3)]

    call_count = 0

    def counting_embed(texts: list[str]) -> list[list[float]]:
        nonlocal call_count
        call_count += 1
        return [[0.0] * 768 for _ in texts]

    mock_engine = MagicMock()
    mock_engine.embed.side_effect = counting_embed

    mock_store = MagicMock()
    mock_store.upsert_batch = AsyncMock()
    mock_store.delete_by_file = AsyncMock()

    original_max = pipeline_mod._MAX_BATCH_TOKENS
    pipeline_mod._MAX_BATCH_TOKENS = 200
    try:
        with (
            patch.object(pipeline_mod, "iter_supported_files", return_value=[Path("test.py")]),
            patch.object(pipeline_mod, "chunk_source", return_value=chunks),
            patch.object(Path, "read_text", return_value="def fn(): pass\n"),
        ):
            indexed_files, total_chunks = await pipeline_mod.index_path(
                target=Path("repo"),
                engine=mock_engine,
                store=mock_store,
                vault_root=Path("vault"),
                file_cache=_NullCache(),
            )
    finally:
        pipeline_mod._MAX_BATCH_TOKENS = original_max

    assert call_count > 1, f"Expected embed to be called >1 times (batched), got {call_count}"
    assert total_chunks == 3, f"Expected 3 total chunks upserted, got {total_chunks}"


@pytest.mark.asyncio
async def test_ingest_file_uses_bounded_batching() -> None:
    """ingest_file must also embed in token-bounded batches (not one unbounded call)."""
    import axon.embedder.pipeline as pipeline_mod

    big_content = "word " * 80  # ~140 estimated tokens
    chunks = [_make_chunk(big_content, f"fn_{i}") for i in range(3)]

    call_count = 0

    def counting_embed(texts: list[str]) -> list[list[float]]:
        nonlocal call_count
        call_count += 1
        return [[0.0] * 768 for _ in texts]

    mock_engine = MagicMock()
    mock_engine.embed.side_effect = counting_embed

    mock_store = MagicMock()
    mock_store.upsert_batch = AsyncMock()

    original_max = pipeline_mod._MAX_BATCH_TOKENS
    pipeline_mod._MAX_BATCH_TOKENS = 200
    try:
        with (
            patch.object(pipeline_mod, "chunk_source", return_value=chunks),
            patch.object(Path, "read_text", return_value="def fn(): pass\n"),
        ):
            result = await pipeline_mod.ingest_file(
                path=Path("test.py"),
                engine=mock_engine,
                store=mock_store,
            )
    finally:
        pipeline_mod._MAX_BATCH_TOKENS = original_max

    assert call_count > 1, f"Expected embed called >1 times in ingest_file, got {call_count}"
    assert result == 3, f"Expected 3 chunks upserted, got {result}"
