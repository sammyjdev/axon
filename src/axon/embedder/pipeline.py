from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from pathlib import Path, PurePath

from axon.context.registry import VALID_CONTEXTS
from axon.embedder.chunker import Chunk, chunk_source
from axon.embedder.engine import EmbedderEngine
from axon.embedder.graph_extractor import build_dependency_records
from axon.embedder.tokens import estimate_tokens as _estimate_tokens
from axon.store.file_cache import FileCache, sha1_of_source
from axon.store.pg_symbol_deps import PostgresSymbolDeps
from axon.store.pg_vector_store import PgVectorStore
from axon.store.vector_common import Chunk as VectorChunk

logger = logging.getLogger(__name__)

_LANGUAGE_MAP = {
    ".java": "java",
    ".py": "python",
    ".ts": "typescript",
    ".md": "markdown",
    ".txt": "text",
}


_CTX_ROOTS = set(VALID_CONTEXTS)
_BATCH_SIZE = 400
# Defense-in-depth for dependency dirs already skipped by gitignore walking and
# EXCLUDED_DIR_NAMES; docs/superpowers/plans is the repository-local exclusion.
_EXCLUDED_PATH_PATTERNS: tuple[str, ...] = (
    "**/docs/superpowers/plans/**",
    "**/node_modules/**",
    "**/.venv/**",
)
_MAX_BATCH_TOKENS: int = int(os.environ.get("AXON_MAX_BATCH_TOKENS", "8192"))
# 0.35 chars/token is a deliberate OVERESTIMATE for input memory safety.
# vector_store.py:153 uses len//4 (=0.25) for output budget where underestimate
# is safe. Here we are bounding onnxruntime INPUT batches to avoid the CPU
# activation arena blowup (Phase 0: batch 64 -> 4.1 GB RSS on CPU).


def _make_token_bounded_batches(
    chunks: list[Chunk],
) -> list[list[Chunk]]:
    """Group chunks into batches that do not exceed _MAX_BATCH_TOKENS.

    A chunk that on its own exceeds the budget is placed in its own batch
    (never dropped). Preserves chunk order.
    """
    batches: list[list[Chunk]] = []
    current: list[Chunk] = []
    current_tokens = 0
    for chunk in chunks:
        tokens = _estimate_tokens(chunk.content)
        if current and current_tokens + tokens > _MAX_BATCH_TOKENS:
            batches.append(current)
            current = []
            current_tokens = 0
        current.append(chunk)
        current_tokens += tokens
    if current:
        batches.append(current)
    return batches


def _embed_in_token_batches(
    engine: EmbedderEngine,
    chunks: list[Chunk],
) -> list[list[float]]:
    """Embed chunks in token-bounded batches to keep the onnxruntime activation
    arena within safe bounds on CPU fallback.

    Order is preserved and every chunk appears in exactly one batch, so the
    returned vectors stay aligned 1:1 with the input chunks list.
    """
    vectors: list[list[float]] = []
    for batch in _make_token_bounded_batches(chunks):
        vectors.extend(engine.embed([c.content for c in batch]))
    return vectors


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


def excluded_path_patterns() -> tuple[str, ...]:
    override = os.environ.get("AXON_INDEX_EXCLUDE")
    if override is None:
        return _EXCLUDED_PATH_PATTERNS
    patterns = tuple(p.strip() for p in override.split(",") if p.strip())
    return patterns or _EXCLUDED_PATH_PATTERNS


def _excluded_path_patterns() -> tuple[str, ...]:
    return excluded_path_patterns()


def _is_excluded_path(path: Path) -> bool:
    """Match exclusion globs against path and parents.

    Parent checks make directory globs exclude arbitrarily deep descendants.
    """
    pure = PurePath(path)
    paths = (pure, *pure.parents)
    return any(
        candidate.match(pattern) for pattern in excluded_path_patterns() for candidate in paths
    )


def is_ctx_indexable(ctx: str, forced_ctx: str | None) -> bool:
    return ctx != "work" or forced_ctx == "work"


def iter_supported_files(
    target: Path,
    *,
    languages: set[str] | None = None,
) -> Iterable[Path]:
    if _is_excluded_path(target):
        return

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
    for path in iter_git_files(target, suffixes=suffixes):
        if not _is_excluded_path(path):
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


async def ingest_file(path: Path, engine: EmbedderEngine, store: PgVectorStore) -> int:
    """Chunks a file, embeds each chunk, and upserts into the vector store.

    Returns the number of chunks upserted.
    """
    if _is_excluded_path(path):
        return 0

    language = _LANGUAGE_MAP.get(path.suffix)
    if language is None:
        return 0

    source = path.read_text(encoding="utf-8", errors="replace")
    chunks: list[Chunk] = chunk_source(source, language, str(path))
    if not chunks:
        return 0

    vectors = _embed_in_token_batches(engine, chunks)

    _occ_counter: dict[str, int] = {}
    vector_chunks = []
    for c, vec in zip(chunks, vectors):
        occ = _occ_counter.get(c.symbol, 0)
        _occ_counter[c.symbol] = occ + 1
        vector_chunks.append(
            VectorChunk(
                id=_chunk_id(str(path), c.symbol, occ),
                vector=vec,
                file_path=c.file_path,
                language=c.language,
                chunk_type=c.chunk_type,
                symbol=c.symbol,
                project=path.parent.name,
                ctx="knowledge",
                content=c.content,
            )
        )

    await store.upsert_batch(vector_chunks)
    logger.info("Indexed %d chunks from %s", len(vector_chunks), path)
    return len(vector_chunks)


async def index_path(
    target: Path,
    *,
    engine: EmbedderEngine,
    store: PgVectorStore,
    vault_root: Path,
    file_cache: FileCache,
    forced_ctx: str | None = None,
    graph_store: PostgresSymbolDeps | None = None,
    languages: set[str] | None = None,
) -> tuple[int, int]:
    """Index all supported files under target.

    file_cache is REQUIRED - no None fallback. Pass a SqliteFileCache for
    production, or a mock/stub for tests.

    Crash-safety (D2): writes status='pending' before any vector-store mutation;
    sets status='done' only after _flush_batch() succeeds. A crash between
    these two points leaves status='pending', which is treated as a hash miss
    on the next run (triggering full re-index of that file).

    Per-ctx reconcile (FIX 1+2): pending_file_meta carries (fp_posix, file_ctx,
    sha1, chunk_count) so that _flush_batch writes done under the SAME ctx used
    when the pending sentinel was written. sha1_maps is loaded lazily per-ctx,
    and found_by_ctx tracks which files were seen per-ctx so that D6 orphan
    cleanup only touches the ctxs actually walked this run.
    """
    files = list(iter_supported_files(target, languages=languages))

    total_chunks = 0
    indexed_files = 0
    pending_batch: list[VectorChunk] = []
    # FIX 1: 4-tuple (fp_posix, file_ctx, sha1, chunk_count) carries per-file
    # ctx so that _flush_batch writes done under the SAME ctx used for pending.
    pending_file_meta: list[tuple[str, str, str, int]] = []
    graph_chunks: list[Chunk] = []

    # FIX 2: lazy per-ctx sha1 maps; found_by_ctx scopes D6 reconcile.
    sha1_maps: dict[str, dict[str, str]] = {}
    found_by_ctx: dict[str, set[str]] = {}

    async def _flush_batch() -> int:
        if not pending_batch:
            return 0
        batch_size = len(pending_batch)
        await store.upsert_batch(list(pending_batch))
        pending_batch.clear()
        # FIX 1: write done under each file's OWN ctx, not a single default ctx.
        for fp, fctx, s1, cc in pending_file_meta:
            await file_cache.set_entry(fp, fctx, s1, cc, status="done")
        pending_file_meta.clear()
        return batch_size

    for file_path in files:
        if _is_excluded_path(file_path):
            continue

        file_ctx = forced_ctx or infer_ctx_from_path(file_path, vault_root)
        if not is_ctx_indexable(file_ctx, forced_ctx):
            continue

        language = _LANGUAGE_MAP.get(file_path.suffix)
        if language is None:
            continue

        fp_posix = file_path.as_posix()

        # FIX 2: load sha1 map for this ctx lazily (one SELECT per ctx per run).
        if file_ctx not in sha1_maps:
            sha1_maps[file_ctx] = await file_cache.get_all_sha1s(file_ctx)

        # FIX 2: track seen files per-ctx for D6 reconcile.
        found_by_ctx.setdefault(file_ctx, set()).add(fp_posix)

        source = file_path.read_text(encoding="utf-8", errors="replace")
        current_sha1 = sha1_of_source(source)

        # FIX 2: skip-check uses per-ctx sha1 map.
        if sha1_maps[file_ctx].get(fp_posix) == current_sha1:
            continue  # file unchanged - skip

        # D2: write crash sentinel BEFORE any vector-store mutation.
        await file_cache.set_entry(fp_posix, file_ctx, current_sha1, 0, status="pending")

        # D4: delete stale points for this file before re-adding.
        await store.delete_by_file(file_ctx, fp_posix)

        chunks: list[Chunk] = chunk_source(source, language, str(file_path))
        if not chunks:
            # No chunks - mark done immediately (empty file is valid).
            await file_cache.set_entry(fp_posix, file_ctx, current_sha1, 0, status="done")
            continue

        # Embed in token-bounded batches to keep the onnxruntime activation
        # arena within safe bounds on CPU fallback (Phase 0: batch 64 -> 4.1 GB RSS).
        vectors = _embed_in_token_batches(engine, chunks)
        _occ: dict[str, int] = {}
        vector_chunks = []
        for c, vec in zip(chunks, vectors):
            occ = _occ.get(c.symbol, 0)
            _occ[c.symbol] = occ + 1
            vector_chunks.append(
                VectorChunk(
                    id=_chunk_id(fp_posix, c.symbol, occ),
                    vector=vec,
                    # FIX 3: store fp_posix so delete_by_file keys match.
                    file_path=fp_posix,
                    language=c.language,
                    chunk_type=c.chunk_type,
                    symbol=c.symbol,
                    project=file_path.parent.name,
                    ctx=file_ctx,
                    content=c.content,
                )
            )

        pending_batch.extend(vector_chunks)
        graph_chunks.extend(chunks)
        # FIX 1: store 4-tuple with per-file ctx.
        pending_file_meta.append((fp_posix, file_ctx, current_sha1, len(chunks)))

        if len(pending_batch) >= _BATCH_SIZE:
            flushed = await _flush_batch()
            total_chunks += flushed

        indexed_files += 1

    # Flush any remaining chunks in the last partial batch.
    flushed = await _flush_batch()
    total_chunks += flushed

    if graph_store is not None and graph_chunks:
        for record in build_dependency_records(graph_chunks):
            await graph_store.upsert_deps(
                record.symbol,
                calls=record.calls,
                called_by=record.called_by,
            )
    # Clean up non-serializable tree-sitter trees before any parallel phase (Spec C handoff).
    # NOT thread-safe: must complete before any parallel step accesses graph_chunks.
    for _chunk in graph_chunks:
        _chunk.metadata.pop("_tree", None)

    # D6: detect files removed from the indexed scope.
    # FIX 2: iterate only ctxs we actually walked; never touch sibling ctxs.
    # FIX (whole-branch review C1): a cached entry is only an orphan if it lives
    # INSIDE the walked target subtree. index_path is also invoked on a single
    # file (watch, per-howto, expansion publish); without this scope check, a
    # single-file index would delete every sibling in the ctx (data loss), since
    # found_by_ctx then holds only that one file. Errs toward not deleting.
    target_posix = Path(target).as_posix()
    target_prefix = target_posix.rstrip("/") + "/"

    def _in_walked_scope(path: str) -> bool:
        return path == target_posix or path.startswith(target_prefix)

    for ctx, found in found_by_ctx.items():
        for cached_path, _ in await file_cache.list_entries(ctx):
            if cached_path not in found and _in_walked_scope(cached_path):
                await store.delete_by_file(ctx, cached_path)
                await file_cache.delete_entry(cached_path, ctx)

    return indexed_files, total_chunks


def _chunk_id(file_path: str, symbol: str, occurrence_index: int) -> str:
    """Stable chunk ID: does not change when lines above the symbol are edited (D1).

    occurrence_index: 0-based count of times this symbol name has appeared
    within the file, to distinguish overloads and sub-chunks (foo[0], foo[1]).
    """
    import uuid

    key = f"{file_path}::{symbol}::{occurrence_index}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))
