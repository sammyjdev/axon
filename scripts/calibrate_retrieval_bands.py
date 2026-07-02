# scripts/calibrate_retrieval_bands.py
"""Sweep LOW/HIGH candidates over the golden set and print the recall delta and
give-up rate per band, so the maintainer can pick constants. Run manually:

    rtk python3 scripts/calibrate_retrieval_bands.py
"""
import asyncio
import dataclasses

import axon.retrieval.self_correct as sc
from axon.benchmark.retrieval_eval import evaluate, load_golden
from axon.mcp.server import (
    _graph_fallback,
    _judge_sufficiency,
    _reformulate_query,
    _retrieve_context,
)

GOLDEN = "tests/benchmark/fixtures/retrieval_golden.json"
CANDIDATES = [(0.25, 0.55), (0.35, 0.65), (0.45, 0.75)]


async def _first_pass(case):
    return await _retrieve_context(
        query=case.query, ctx=case.ctx, language=None,
        max_depth=2, max_nodes=25, max_tokens=1200,
    )


async def _correct(case, cc, pack, hits):
    async def _retry(q):
        return await _retrieve_context(
            query=q, ctx=case.ctx, language=None,
            max_depth=2, max_nodes=25, max_tokens=1200,
        )
    return await sc.correct_retrieval(
        case.query, case.ctx, cc, pack, hits,
        retrieve_fn=_retry, judge_fn=_judge_sufficiency,
        reformulate_fn=_reformulate_query, graph_fn=_graph_fallback,
        augment_pack_fn=lambda pack, graph_text: dataclasses.replace(
            pack, segments=pack.segments + (graph_text,)
        ),
    )


async def main():
    cases = load_golden(GOLDEN)
    for low, high in CANDIDATES:
        sc.LOW, sc.HIGH = low, high
        report = await evaluate(cases, _first_pass, _correct)
        print(f"LOW={low} HIGH={high} -> {report}")


if __name__ == "__main__":
    asyncio.run(main())
