from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable
from pathlib import Path

from axon.context.registry import VALID_CONTEXTS
from axon.embedder.chunker import Chunk, chunk_source
from axon.embedder.engine import EmbedderEngine
from axon.embedder.graph_extractor import build_dependency_records
from axon.store.graph_store import GraphStore
from axon.store.vector_store import Chunk as VectorChunk
from axon.store.vector_store import VectorStore

logger = logging.getLogger(__name__)

_LANGUAGE_MAP = {
    ".java": "java",
    ".py": "python",
    ".ts": "typescript",
    ".md": "markdown",
    ".txt": "text",
}


_CTX_ROOTS = set(VALID_CONTEXTS)
_FILE_HASH_CACHE: dict[str, str] = {}
_BATCH_SIZE = 400
# SYNC NOTE: this set must be kept in sync with _EXCLUDED_DIR_NAMES in
# axon/repo/file_walk.py. If you add a directory to one, add it to both.
EXCLUDED_DIR_NAMES = {
    ".aws-sam",
    ".git",
    ".gradle",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".turbo",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    # Catch any Python virtualenv regardless of its top-level directory name
    # (e.g. a renamed ".venv_hidden"): all dependency files live under one of
    # these segments, so excluding them is name-independent.
    "dist-packages",
    "site-packages",
    "node_modules",
    "target",
    "venv",
}


def _language_for_suffix(suffix: str) -> str | None:
    return _LANGUAGE_MAP.get(suffix)


def iter_supported_files(
    target: Path,
    *,
    languages: set[str] | None = None,
) -> Iterable[Path]:
    if target.is_file():
        language = _language_for_suffix(target.suffix)
        if language and (languages is None or language in languages):
            yield target
        return

    from axon.repo.file_walk import iter_git_files
    suffixes = {
        s for s, lang in _LANGUAGE_MAP.items()
        if languages is None or lang in languages
    }
    yield from iter_git_files(target, suffixes=suffixes)


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
    graph_store: GraphStore | None = None,
    languages: set[str] | None = None,
) -> tuple[int, int]:
    files = list(iter_supported_files(target, languages=languages))
    total_chunks = 0
    indexed_files = 0
    pending_batch: list[VectorChunk] = []
    graph_chunks: list[Chunk] = []

    async def _flush_batch() -> int:
        if not pending_batch:
            return 0
        batch_size = len(pending_batch)
        await store.upsert_batch(list(pending_batch))
        pending_batch.clear()
        return batch_size

    for file_path in files:
        file_ctx = forced_ctx or infer_ctx_from_path(file_path, vault_root)
        if file_ctx == "work" and forced_ctx != "work":
            continue

        language = _LANGUAGE_MAP.get(file_path.suffix)
        if language is None:
            continue

        source = file_path.read_text(encoding="utf-8", errors="replace")
        source_hash = hashlib.sha1(source.encode("utf-8")).hexdigest()
        cache_key = str(file_path.resolve())
        if _FILE_HASH_CACHE.get(cache_key) == source_hash:
            continue

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

        pending_batch.extend(vector_chunks)
        graph_chunks.extend(chunks)
        if len(pending_batch) >= _BATCH_SIZE:
            await _flush_batch()

        _FILE_HASH_CACHE[cache_key] = source_hash
        indexed_files += 1
        total_chunks += len(vector_chunks)

    await _flush_batch()
    if graph_store is not None and graph_chunks:
        for record in build_dependency_records(graph_chunks):
            await graph_store.upsert_deps(
                record.symbol,
                calls=record.calls,
                called_by=record.called_by,
            )
    return indexed_files, total_chunks


def _chunk_id(path: Path, chunk: Chunk) -> str:
    """Stable ID for a chunk: hash of file path + symbol + start_line."""
    import uuid

    key = f"{path}::{chunk.symbol}::{chunk.start_line}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))
