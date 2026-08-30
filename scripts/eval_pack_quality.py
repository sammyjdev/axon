"""Does the pack contain the files the work actually touched?

Baseline measured 2026-08-29 (n=60, top_k=8, four collections): hit rate 0.583,
coverage 0.384. See docs/superpowers/specs/2026-08-29-retrieval-pack-quality-design.md.

Usage:
    AXON_PG_URL=postgresql://axon:axon@localhost:5434/axon \
        HF_HUB_DISABLE_XET=1 python3 scripts/eval_pack_quality.py --top-k 8
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
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
    args = parser.parse_args()

    from axon.benchmark.pack_eval import basenames, load_cases, pack_coverage, pack_hit
    from axon.embedder.engine import EmbedderEngine
    from axon.store.pg_vector_store import PgVectorStore

    cases = load_cases(args.fixture)
    engine = EmbedderEngine()
    store = PgVectorStore(dsn=args.dsn)
    hits = 0
    coverage = 0.0
    try:
        for case in cases:
            results = await store.search(
                query_vector=engine.embed_one(case.query),
                query=case.query,
                collections=COLLECTIONS,
                top_k=args.top_k,
                max_nodes=args.top_k,
                max_tokens=10**9,
            )
            paths = [str((h.get("payload") or {}).get("file_path", "")) for h in results]
            expected = basenames(case.expected_files)
            hits += 1 if pack_hit(expected, paths) else 0
            coverage += pack_coverage(expected, paths)
    finally:
        await store.close()

    n = len(cases)
    print(f"cases: {n}   top_k: {args.top_k}")
    print(f"hit rate: {hits}/{n} = {hits / n:.3f}")
    print(f"coverage: {coverage / n:.3f}")


if __name__ == "__main__":
    asyncio.run(main())
