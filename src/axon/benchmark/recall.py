"""Semantic recall guard for the AXON code-search pipeline.

Uses a real GPU-backed EmbedderEngine + live Qdrant to verify that the
code-chunk index returns the expected symbol in the top-k results.
"""
from __future__ import annotations

import time
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from axon.benchmark.contracts import BenchmarkCheck, BenchmarkResult, BenchmarkRunSummary
from axon.embedder.chunker import chunk_source
from axon.embedder.engine import EmbedderEngine

TEMP_COLLECTION = "_recall_guard_tmp"

_BATCH_SIZE = 64


def _make_store(dsn: str | None = None):
    """Return a vector store instance for the recall harness.

    If dsn is provided, return a PgVectorStore (for test-only smoke paths).
    Otherwise return a QdrantClient using the default URL (preserves existing
    production behavior unchanged).
    """
    if dsn is not None:
        from axon.store.pg_vector_store import PgVectorStore

        return PgVectorStore(dsn)
    return QdrantClient()


def index_corpus(
    client: QdrantClient,
    engine: EmbedderEngine,
    *,
    src_root: Path,
    repo_root: Path,
    collection: str = TEMP_COLLECTION,
) -> int:
    """Index all .py files under src_root into Qdrant and return the number of points.

    The collection is (re)created fresh on every call.
    """
    py_files = sorted(src_root.rglob("*.py"))

    # Gather all chunks across all files
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

    if not all_chunks:
        return 0

    # (Re)create the collection fresh
    existing = {c.name for c in client.get_collections().collections}
    if collection in existing:
        client.delete_collection(collection_name=collection)
    client.create_collection(
        collection_name=collection,
        vectors_config=VectorParams(size=768, distance=Distance.COSINE),
    )

    # Embed in batches and upsert
    total_upserted = 0
    point_id = 0
    for batch_start in range(0, len(all_chunks), _BATCH_SIZE):
        batch = all_chunks[batch_start : batch_start + _BATCH_SIZE]
        contents = [c.content for c in batch]
        vectors = engine.embed(contents)

        points = []
        for chunk, vec in zip(batch, vectors):
            points.append(
                PointStruct(
                    id=point_id,
                    vector=vec,
                    payload={
                        "file_path": chunk.file_path,
                        "symbol": chunk.symbol,
                        "chunk_type": chunk.chunk_type,
                    },
                )
            )
            point_id += 1

        client.upsert(collection_name=collection, points=points)
        total_upserted += len(points)

    return total_upserted


def _is_hit(payload: dict, expected_file: str, expected_symbol: str) -> bool:
    """Return True when payload matches the expected file and symbol.

    The startswith guard handles cap-split symbols like name[0].
    """
    if payload.get("file_path") != expected_file:
        return False
    sym = payload.get("symbol", "")
    return sym == expected_symbol or sym.startswith(expected_symbol + "[")


def run_recall_guard(
    golden_set: list[dict],
    engine: EmbedderEngine,
    client: QdrantClient,
    *,
    collection: str = TEMP_COLLECTION,
    top_k: int = 5,
) -> tuple[BenchmarkRunSummary, dict]:
    """Run every query in golden_set against the indexed collection.

    Returns (BenchmarkRunSummary, metrics_dict) where metrics_dict contains:
      - recall_top1: fraction of queries where rank == 1
      - recall_top3: fraction of queries where rank <= 3
      - results_by_query: per-query detail dict
    """
    benchmark_results = []
    results_by_query: dict[str, dict] = {}

    for entry in golden_set:
        qid = entry["id"]
        query_text = entry["query"]
        expected_file = entry["expected_file"]
        expected_symbol = entry["expected_symbol"]

        t0 = time.perf_counter()
        query_vec = engine.embed_one(query_text)

        hits = client.query_points(
            collection_name=collection,
            query=query_vec,
            limit=top_k,
        ).points
        duration_ms = (time.perf_counter() - t0) * 1000.0

        rank: int | None = None
        top_score: float = hits[0].score if hits else 0.0

        for i, hit in enumerate(hits, start=1):
            payload = hit.payload or {}
            if _is_hit(payload, expected_file, expected_symbol):
                rank = i
                break

        hit_top1 = rank == 1
        hit_top3 = rank is not None and rank <= 3

        check = BenchmarkCheck(
            name="hit_top1",
            passed=hit_top1,
            expected=f"rank=1 file={expected_file} symbol={expected_symbol}",
            actual=f"rank={rank} top_score={top_score:.4f}",
        )
        benchmark_results.append(
            BenchmarkResult(
                suite="recall_guard",
                name=qid,
                duration_ms=duration_ms,
                checks=(check,),
            )
        )

        results_by_query[qid] = {
            "query": query_text,
            "expected_file": expected_file,
            "expected_symbol": expected_symbol,
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


async def index_corpus_pg_smoke(dsn: str) -> str:
    """Test-only smoke helper: upsert a 'near' and a 'far' chunk via PgVectorStore,
    run a search, and return the id of the top result.

    No EmbedderEngine or GPU is required - vectors are hand-crafted so the
    result is deterministic. The 'near' chunk has a vector that is identical to
    the query (cosine similarity 1.0); the 'far' chunk is orthogonal (similarity 0.0).
    """
    from axon.store.pg_vector_store import PgVectorStore
    from axon.store.vector_store import VECTOR_SIZE, Chunk

    store = PgVectorStore(dsn=dsn)
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
