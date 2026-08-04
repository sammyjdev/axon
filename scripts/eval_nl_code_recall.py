"""Re-run the #45 sweep: do natural-language queries retrieve code, or docs?

Baseline to beat (issue #45, 2026-07-02, bge-small-en / dim 384): 6/24 cases
retrieved the expected code symbol. Each case is searched in its own ctx, the
way the original sweep ran.

Usage:
    AXON_PG_URL=postgresql://axon:axon@localhost:5434/axon \
        python3 scripts/eval_nl_code_recall.py --top-k 5
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

FIXTURE = REPO_ROOT / "tests" / "benchmark" / "fixtures" / "retrieval_golden.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Recall of code symbols for NL queries (#45).")
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--dsn",
        default=os.environ.get("AXON_PG_URL", "postgresql://axon:axon@localhost:5434/axon"),
    )
    return parser


async def _search(store, engine, case: dict, top_k: int, *, hybrid: bool):
    old = os.environ.get("AXON_HYBRID_SEARCH")
    try:
        if hybrid:
            os.environ["AXON_HYBRID_SEARCH"] = "1"
        else:
            os.environ.pop("AXON_HYBRID_SEARCH", None)
        return await store.search(
            query_vector=engine.embed_one(case["query"]),
            query=case["query"],
            collections=[case["ctx"]],
            top_k=top_k,
            max_nodes=top_k,
            max_tokens=10**9,
        )
    finally:
        if old is None:
            os.environ.pop("AXON_HYBRID_SEARCH", None)
        else:
            os.environ["AXON_HYBRID_SEARCH"] = old


def _doc_share(hits: list[dict], top_k: int) -> float:
    """Fraction of the top-k that is prose. The literal #45 symptom."""
    window = hits[:top_k]
    if not window:
        return 0.0
    docs = sum(1 for h in window if ((h.get("payload") or {}).get("chunk_type")) == "section")
    return docs / len(window)


async def _run(args: argparse.Namespace) -> None:
    from axon.benchmark.retrieval_eval import recall, symbols_of
    from axon.embedder.engine import EmbedderEngine
    from axon.store.pg_vector_store import PgVectorStore

    cases = [c for c in json.loads(args.fixture.read_text(encoding="utf-8"))]
    engine = EmbedderEngine()
    store = PgVectorStore(dsn=args.dsn)
    tallies = {"dense": [0.0, 0.0], "hybrid": [0.0, 0.0]}  # [recall sum, doc-share sum]
    try:
        await store.ensure_collections()
        for case in cases:
            expected = frozenset(case["expected_symbols"])
            line = [f"{case['ctx']:<9} {case['query'][:58]:<58}"]
            for arm in ("dense", "hybrid"):
                hits = await _search(store, engine, case, args.top_k, hybrid=arm == "hybrid")
                r = recall(expected, hits)
                tallies[arm][0] += r
                tallies[arm][1] += _doc_share(hits, args.top_k)
                line.append(f"{arm}={'HIT' if r else 'miss'}")
            print(" ".join(line))
            # symbols_of() dedups: fewer symbols than hits is normal, not truncation.
            print(f"    expected={sorted(expected)} hybrid_symbols={sorted(symbols_of(hits))}")
    finally:
        await store.close()

    n = len(cases)
    print("\nbaseline (#45, bge-small-en): 6/24 = 0.250")
    for arm in ("dense", "hybrid"):
        r, d = tallies[arm]
        print(
            f"{arm:<7} recall@{args.top_k}: {r:.1f}/{n} = {r / n:.3f}"
            f"   doc-share of top-k: {d / n:.3f}"
        )


def main() -> None:
    asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    main()
