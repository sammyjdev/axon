"""Compare dense-only vs hybrid hit@3 for exact-term queries.

Usage:
    AXON_PG_URL=postgresql://axon:axon@localhost:5434/axon \
        python3 scripts/eval_exact_terms.py
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

FIXTURE = REPO_ROOT / "tests" / "benchmark" / "fixtures" / "exact_term_queries.json"
DEFAULT_CONTEXTS = ("personal", "career", "knowledge", "saas")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Report dense-only vs AXON_HYBRID_SEARCH=1 hit@3 for exact terms."
    )
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument(
        "--ctx",
        action="append",
        dest="contexts",
        help="Context to search. Repeatable. Defaults to personal/career/knowledge/saas.",
    )
    parser.add_argument(
        "--dsn",
        default=os.environ.get("AXON_PG_URL", "postgresql://axon:axon@localhost:5434/axon"),
    )
    return parser


def _hit(hits: list[dict], expected: str, top_k: int) -> bool:
    expected_lower = expected.lower()
    for hit in hits[:top_k]:
        payload = hit.get("payload") or {}
        if expected_lower in str(payload.get("file_path", "")).lower():
            return True
    return False


def _paths(hits: list[dict], top_k: int) -> str:
    paths = [
        str((hit.get("payload") or {}).get("file_path", ""))
        for hit in hits[:top_k]
    ]
    return ", ".join(paths) if paths else "(none)"


async def _search(store, engine, query: str, contexts: list[str], top_k: int, *, hybrid: bool):
    old = os.environ.get("AXON_HYBRID_SEARCH")
    try:
        if hybrid:
            os.environ["AXON_HYBRID_SEARCH"] = "1"
        else:
            os.environ.pop("AXON_HYBRID_SEARCH", None)
        return await store.search(
            query_vector=engine.embed_one(query),
            query=query,
            collections=contexts,
            top_k=top_k,
            max_nodes=top_k,
            max_tokens=10**9,
        )
    finally:
        if old is None:
            os.environ.pop("AXON_HYBRID_SEARCH", None)
        else:
            os.environ["AXON_HYBRID_SEARCH"] = old


async def _run(args: argparse.Namespace) -> None:
    from axon.embedder.engine import EmbedderEngine
    from axon.store.pg_vector_store import PgVectorStore

    cases = json.loads(args.fixture.read_text(encoding="utf-8"))
    contexts = args.contexts or list(DEFAULT_CONTEXTS)
    engine = EmbedderEngine()
    store = PgVectorStore(dsn=args.dsn)
    dense_hits = 0
    hybrid_hits = 0
    try:
        await store.ensure_collections()
        for case in cases:
            query = case["query"]
            expected = case["expected_file_substring"]
            dense = await _search(store, engine, query, contexts, args.top_k, hybrid=False)
            hybrid = await _search(store, engine, query, contexts, args.top_k, hybrid=True)
            dense_ok = _hit(dense, expected, args.top_k)
            hybrid_ok = _hit(hybrid, expected, args.top_k)
            dense_hits += int(dense_ok)
            hybrid_hits += int(hybrid_ok)
            print(
                f"{query}: dense={'hit' if dense_ok else 'miss'} "
                f"hybrid={'hit' if hybrid_ok else 'miss'} expected=*{expected}*"
            )
            print(f"  dense:  {_paths(dense, args.top_k)}")
            print(f"  hybrid: {_paths(hybrid, args.top_k)}")
    finally:
        await store.close()

    total = len(cases)
    print(f"\ndense hit@{args.top_k}: {dense_hits}/{total} ({dense_hits / total:.3f})")
    print(f"hybrid hit@{args.top_k}: {hybrid_hits}/{total} ({hybrid_hits / total:.3f})")


def main() -> None:
    asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    main()
