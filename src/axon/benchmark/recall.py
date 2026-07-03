"""Semantic recall guard for the AXON code-search pipeline.

Uses a real GPU-backed EmbedderEngine + live pgvector store to verify that the
code-chunk index returns the expected symbol in the top-k results.

Entry points: index_corpus_pg / run_recall_guard_pg (pgvector, async).
"""
from __future__ import annotations

import time
from pathlib import Path

from axon.benchmark.contracts import BenchmarkCheck, BenchmarkResult, BenchmarkRunSummary
from axon.embedder.chunker import chunk_source
from axon.embedder.engine import EmbedderEngine

RECALL_TABLE = "recall_embeddings"

_BATCH_SIZE = 64


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _gather_chunks(src_root: Path, repo_root: Path) -> list:
    """Collect all parsed chunks from .py files under src_root."""
    py_files = sorted(src_root.rglob("*.py"))
    all_chunks = []
    for py_file in py_files:
        try:
            source = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel_path = py_file.relative_to(repo_root).as_posix()
        try:
            chunks = chunk_source(source, "python", rel_path)
        except Exception:  # noqa: BLE001
            continue
        all_chunks.extend(chunks)
    return all_chunks


def _is_hit(payload: dict, expected_file: str, expected_symbol: str) -> bool:
    """Return True when payload matches the expected file and symbol.

    The startswith guard handles cap-split symbols like name[0].
    """
    if payload.get("file_path") != expected_file:
        return False
    sym = payload.get("symbol", "")
    return sym == expected_symbol or sym.startswith(expected_symbol + "[")


def _assemble_metrics(
    golden_set: list[dict],
    per_query: dict,
) -> tuple[BenchmarkRunSummary, dict]:
    """Build BenchmarkRunSummary and metrics dict from per-query result data.

    per_query maps qid -> {query, expected_file, expected_symbol, rank,
    top_score, duration_ms}.
    """
    benchmark_results = []
    results_by_query: dict[str, dict] = {}

    for entry in golden_set:
        qid = entry["id"]
        q = per_query[qid]

        rank = q["rank"]
        top_score = q["top_score"]
        hit_top1 = rank == 1
        hit_top3 = rank is not None and rank <= 3

        check = BenchmarkCheck(
            name="hit_top1",
            passed=hit_top1,
            expected=f"rank=1 file={q['expected_file']} symbol={q['expected_symbol']}",
            actual=f"rank={rank} top_score={top_score:.4f}",
        )
        benchmark_results.append(
            BenchmarkResult(
                suite="recall_guard",
                name=qid,
                duration_ms=q["duration_ms"],
                checks=(check,),
            )
        )

        results_by_query[qid] = {
            "query": q["query"],
            "expected_file": q["expected_file"],
            "expected_symbol": q["expected_symbol"],
            "rank": rank,
            "top_score": top_score,
            "hit_top1": hit_top1,
            "hit_top3": hit_top3,
        }

    summary = BenchmarkRunSummary(results=tuple(benchmark_results))
    n = len(golden_set)
    recall_top1 = sum(1 for r in results_by_query.values() if r["hit_top1"]) / n if n else 0.0
    recall_top3 = sum(1 for r in results_by_query.values() if r["hit_top3"]) / n if n else 0.0

    metrics = {
        "recall_top1": recall_top1,
        "recall_top3": recall_top3,
        "results_by_query": results_by_query,
    }
    return summary, metrics


# ---------------------------------------------------------------------------
# pgvector path (async)
# ---------------------------------------------------------------------------


async def index_corpus_pg(
    store,
    engine: EmbedderEngine,
    *,
    src_root: Path,
    repo_root: Path,
    ctx: str = "knowledge",
) -> int:
    """Index all .py files under src_root into a PgVectorStore and return the point count.

    The embeddings table is truncated before indexing to ensure a fresh run.
    """
    from axon.store.vector_common import Chunk

    all_chunks = _gather_chunks(src_root, repo_root)
    if not all_chunks:
        return 0

    await store.ensure_collections()

    # Truncate for a fresh index run (use store._table so we never touch production data)
    async with store._pool.acquire() as con:
        await con.execute(f"TRUNCATE {store._table}")

    total_upserted = 0
    point_id = 0
    for batch_start in range(0, len(all_chunks), _BATCH_SIZE):
        batch = all_chunks[batch_start : batch_start + _BATCH_SIZE]
        vectors = engine.embed([c.content for c in batch])

        store_chunks = []
        for chunk, vec in zip(batch, vectors):
            store_chunks.append(
                Chunk(
                    id=str(point_id),
                    vector=list(vec),
                    file_path=chunk.file_path,
                    language="python",
                    chunk_type=chunk.chunk_type,
                    symbol=chunk.symbol,
                    project="recall",
                    ctx=ctx,
                    content=chunk.content,
                )
            )
            point_id += 1

        await store.upsert_batch(store_chunks)
        total_upserted += len(store_chunks)

    return total_upserted


async def run_recall_guard_pg(
    golden_set: list[dict],
    engine,
    store,
    *,
    ctx: str = "knowledge",
    top_k: int = 5,
) -> tuple[BenchmarkRunSummary, dict]:
    """Run every query in golden_set against a PgVectorStore collection.

    Returns (BenchmarkRunSummary, metrics_dict) - same shape as run_recall_guard.
    """
    per_query: dict[str, dict] = {}

    for entry in golden_set:
        qid = entry["id"]
        query_text = entry["query"]
        expected_file = entry["expected_file"]
        expected_symbol = entry["expected_symbol"]

        t0 = time.perf_counter()
        query_vec = engine.embed_one(query_text)
        # Disable the token-budget / max_nodes truncation so the harness compares
        # raw cosine top_k without the _rank_and_limit filter. Without this the
        # pgvector path could drop in-top_k hits purely on the 1200-token budget
        # and report a false regression vs the historical baseline.
        hits = await store.search(
            list(query_vec),
            query=query_text,
            collections=[ctx],
            top_k=top_k,
            max_nodes=10**9,
            max_tokens=10**9,
        )
        duration_ms = (time.perf_counter() - t0) * 1000.0

        rank: int | None = None
        top_score: float = hits[0]["score"] if hits else 0.0

        for i, hit in enumerate(hits, start=1):
            payload = hit.get("payload") or {}
            if _is_hit(payload, expected_file, expected_symbol):
                rank = i
                break

        per_query[qid] = {
            "query": query_text,
            "expected_file": expected_file,
            "expected_symbol": expected_symbol,
            "rank": rank,
            "top_score": top_score,
            "duration_ms": duration_ms,
        }

    return _assemble_metrics(golden_set, per_query)


# ---------------------------------------------------------------------------
# Test-only smoke helper (DO NOT REMOVE - used by test_recall_pgvector_path.py)
# ---------------------------------------------------------------------------


async def index_corpus_pg_smoke(dsn: str) -> str:
    """Test-only smoke helper: upsert a 'near' and a 'far' chunk via PgVectorStore,
    run a search, and return the id of the top result.

    No EmbedderEngine or GPU is required - vectors are hand-crafted so the
    result is deterministic. The 'near' chunk has a vector that is identical to
    the query (cosine similarity 1.0); the 'far' chunk is orthogonal (similarity 0.0).
    """
    from axon.store.pg_vector_store import PgVectorStore
    from axon.store.vector_common import VECTOR_SIZE, Chunk

    store = PgVectorStore(dsn=dsn, table=RECALL_TABLE)
    try:
        await store.ensure_collections()

        # Build hand-crafted chunks - no GPU needed
        near_vector = [1.0] + [0.0] * (VECTOR_SIZE - 1)
        far_vector = [0.0, 1.0] + [0.0] * (VECTOR_SIZE - 2)

        near_chunk = Chunk(
            id="near",
            vector=near_vector,
            file_path="smoke/near.py",
            language="python",
            chunk_type="function",
            symbol="near_fn",
            project="smoke",
            ctx="knowledge",
            content="def near_fn(): pass",
        )
        far_chunk = Chunk(
            id="far",
            vector=far_vector,
            file_path="smoke/far.py",
            language="python",
            chunk_type="function",
            symbol="far_fn",
            project="smoke",
            ctx="knowledge",
            content="def far_fn(): pass",
        )

        await store.upsert_batch([near_chunk, far_chunk])

        # Query with the same vector as "near" - it must rank first
        hits = await store.search(
            query_vector=near_vector,
            collections=["knowledge"],
            top_k=5,
        )
        return hits[0]["id"] if hits else ""
    finally:
        await store.close()
