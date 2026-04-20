from __future__ import annotations

import logging
from pathlib import Path

from prometheus.embedder.chunker import Chunk, chunk_source
from prometheus.embedder.engine import EmbedderEngine
from prometheus.store.vector import VectorStore

logger = logging.getLogger(__name__)

_LANGUAGE_MAP = {
    ".java": "java",
    ".py": "python",
    ".ts": "typescript",
}


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

    points = [
        {
            "id": _chunk_id(path, c),
            "vector": vec,
            "payload": {
                "symbol": c.symbol,
                "chunk_type": c.chunk_type,
                "start_line": c.start_line,
                "end_line": c.end_line,
                "file_path": c.file_path,
                "language": c.language,
                **c.metadata,
            },
        }
        for c, vec in zip(chunks, vectors)
    ]

    await store.upsert(points)
    logger.info("Indexed %d chunks from %s", len(points), path)
    return len(points)


def _chunk_id(path: Path, chunk: Chunk) -> str:
    """Stable ID for a chunk: hash of file path + symbol + start_line."""
    import hashlib
    key = f"{path}::{chunk.symbol}::{chunk.start_line}"
    return hashlib.sha1(key.encode()).hexdigest()
