"""Does the pack contain the files the work actually touched?

The default (store-only) arm is the frozen ruler: measured Task 1 baseline
(n=120, top_k=8, four collections) is hit rate 0.625, coverage 0.402. Every
future run must remain comparable to that baseline.

With --pack-path, the script measures the full assembled pack produced by
axon.mcp.server._retrieve_context (including deduplication and cross-encoder
reranking).

The two arms are not interchangeable: store-only measures raw vector search
recall, while --pack-path measures the actual production retrieval pipeline.

Usage:
    # Store-only baseline (default):
    AXON_PG_URL=postgresql://axon:axon@localhost:5434/axon \
        python3 scripts/eval_pack_quality.py --top-k 8

    # Assembled pack path:
    AXON_PG_URL=postgresql://axon:axon@localhost:5434/axon \
        python3 scripts/eval_pack_quality.py --top-k 8 --pack-path
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

FIXTURE = REPO_ROOT / "tests" / "benchmark" / "fixtures" / "pack_quality_golden.json"
COLLECTIONS = ["personal", "career", "knowledge", "saas"]


async def main() -> None:
    parser = argparse.ArgumentParser(description="Pack quality against the committed fixture.")
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument(
        "--dsn", default=os.environ.get("AXON_PG_URL", "postgresql://axon:axon@localhost:5434/axon")
    )
    parser.add_argument(
        "--pack-path",
        action="store_true",
        help="Score the assembled pack via _retrieve_context instead of raw store.search.",
    )
    args = parser.parse_args()

    from axon.benchmark.pack_eval import basenames, load_cases, pack_coverage, pack_hit

    cases = load_cases(args.fixture)
    store = None

    if args.pack_path:
        # Isolate telemetry before importing server: _retrieve_context writes recall
        # telemetry on every call. Running 120 synthetic benchmark queries would
        # append 120 rows to the operator's real data/recall/chunks.jsonl measurement
        # corpus. Pointing AXON_ENGINE at a fresh tempdir isolates where telemetry is
        # written without affecting where vectors are read from (AXON_PG_URL / --dsn).
        os.environ["AXON_ENGINE"] = tempfile.mkdtemp(prefix="axon-eval-pack-")
        os.environ["HF_HUB_DISABLE_XET"] = "1"
        os.environ["AXON_PG_URL"] = args.dsn

        from axon.mcp import server

        async def retrieve(query: str) -> list[dict]:
            _, _, results = await server._retrieve_context(
                query=query,
                ctx=None,
                language=None,
                max_depth=1,
                max_nodes=args.top_k,
                max_tokens=10**9,
            )
            return results
    else:
        from axon.embedder.engine import EmbedderEngine
        from axon.store.pg_vector_store import PgVectorStore

        engine = EmbedderEngine()
        store = PgVectorStore(dsn=args.dsn)

        async def retrieve(query: str) -> list[dict]:
            return await store.search(
                query_vector=engine.embed_one(query),
                query=query,
                collections=COLLECTIONS,
                top_k=args.top_k,
                max_nodes=args.top_k,
                max_tokens=10**9,
            )

    hits = 0
    coverage = 0.0
    try:
        for case in cases:
            results = await retrieve(case.query)
            paths = [str((h.get("payload") or {}).get("file_path", "")) for h in results]
            expected = basenames(case.expected_files)
            hits += 1 if pack_hit(expected, paths) else 0
            coverage += pack_coverage(expected, paths)
    finally:
        if store is not None:
            await store.close()

    n = len(cases)
    mode = "pack-path" if args.pack_path else "store-only"
    print(f"mode: {mode}   cases: {n}   top_k: {args.top_k}")
    print(f"hit rate: {hits}/{n} = {hits / n:.3f}")
    print(f"coverage: {coverage / n:.3f}")


if __name__ == "__main__":
    asyncio.run(main())
