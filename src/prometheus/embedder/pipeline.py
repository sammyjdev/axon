from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from prometheus.embedder.chunker import Chunk, chunk_source
from prometheus.embedder.engine import EmbedderEngine
from prometheus.store.vector_store import Chunk as VectorChunk
from prometheus.store.vector_store import VectorStore

logger = logging.getLogger(__name__)

_LANGUAGE_MAP = {
    ".java": "java",
    ".py": "python",
    ".ts": "typescript",
    ".md": "markdown",
    ".txt": "text",
}


_CTX_ROOTS = {"personal", "career", "knowledge", "work"}


def iter_supported_files(target: Path) -> Iterable[Path]:
    if target.is_file():
        if target.suffix in _LANGUAGE_MAP:
            yield target
        return

    for path in target.rglob("*"):
        if path.is_file() and path.suffix in _LANGUAGE_MAP:
            yield path


def infer_ctx_from_path(path: Path, vault_root: Path) -> str:
    try:
        rel = path.resolve().relative_to(vault_root.resolve())
    except ValueError:
        return "knowledge"

    root = rel.parts[0] if rel.parts else "knowledge"
    if root in _CTX_ROOTS:
        return root
    return "knowledge"


async def ingest_file(path: Path, engine: EmbedderEngine, store: VectorStore) -> int:
    """Chunks a file, embeds each chunk, and upserts into the vector store.

    Returns the number of chunks upserted.
    """
    language = _LANGUAGE_MAP.get(path.suffix)
    if language is None:
        return 0

    source = path.read_text(encoding="utf-8", errors="replace")
    chunks: list[Chunk] = chunk_source(source, language, str(path))
    if not chunks:
        return 0

    texts = [c.content for c in chunks]
    vectors = engine.embed(texts)

    vector_chunks = [
        VectorChunk(
            id=_chunk_id(path, c),
            vector=vec,
            file_path=c.file_path,
            language=c.language,
            chunk_type=c.chunk_type,
            symbol=c.symbol,
            project=path.parent.name,
            ctx="knowledge",
            content=c.content,
        )
        for c, vec in zip(chunks, vectors)
    ]

    await store.upsert_batch(vector_chunks)
    logger.info("Indexed %d chunks from %s", len(vector_chunks), path)
    return len(vector_chunks)


async def index_path(
    target: Path,
    *,
    engine: EmbedderEngine,
    store: VectorStore,
    vault_root: Path,
    forced_ctx: str | None = None,
) -> tuple[int, int]:
    files = list(iter_supported_files(target))
    total_chunks = 0
    indexed_files = 0

    for file_path in files:
        file_ctx = forced_ctx or infer_ctx_from_path(file_path, vault_root)
        if file_ctx == "work" and forced_ctx != "work":
            continue

        language = _LANGUAGE_MAP.get(file_path.suffix)
        if language is None:
            continue

        source = file_path.read_text(encoding="utf-8", errors="replace")
        chunks: list[Chunk] = chunk_source(source, language, str(file_path))
        if not chunks:
            continue

        vectors = engine.embed([c.content for c in chunks])
        vector_chunks = [
            VectorChunk(
                id=_chunk_id(file_path, c),
                vector=vec,
                file_path=c.file_path,
                language=c.language,
                chunk_type=c.chunk_type,
                symbol=c.symbol,
                project=file_path.parent.name,
                ctx=file_ctx,
                content=c.content,
            )
            for c, vec in zip(chunks, vectors)
        ]

        await store.upsert_batch(vector_chunks)
        indexed_files += 1
        total_chunks += len(vector_chunks)

    return indexed_files, total_chunks


def _chunk_id(path: Path, chunk: Chunk) -> str:
    """Stable ID for a chunk: hash of file path + symbol + start_line."""
    import uuid

    key = f"{path}::{chunk.symbol}::{chunk.start_line}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))
