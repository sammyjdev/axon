"""Retrieval-quality benchmark for the self-correcting loop. Measures recall@k
before vs after correction over a golden set. Distinct from model_eval.py, which
compares models, not retrieval.

See docs/superpowers/specs/2026-07-01-agentic-retrieval-design.md (D-E).
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from axon.retrieval.self_correct import CorrectionResult


@dataclass(frozen=True)
class GoldenCase:
    query: str
    ctx: str
    expected_symbols: frozenset[str]


def load_golden(path: str) -> list[GoldenCase]:
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    return [
        GoldenCase(c["query"], c["ctx"], frozenset(c["expected_symbols"]))
        for c in raw
    ]


def symbols_of(hits: list[dict]) -> set[str]:
    out: set[str] = set()
    for h in hits:
        sym = (h.get("payload") or {}).get("symbol")
        if sym:
            out.add(sym)
    return out


def recall(expected: frozenset[str], hits: list[dict]) -> float:
    if not expected:
        return 1.0
    found = expected & symbols_of(hits)
    return len(found) / len(expected)


async def evaluate(
    cases: list[GoldenCase],
    first_pass_fn: Callable[[GoldenCase], Awaitable[tuple[str, object, list[dict]]]],
    correct_fn: Callable[[GoldenCase, str, object, list[dict]], Awaitable[CorrectionResult]],
) -> dict:
    n = len(cases)
    if n == 0:
        return {"recall_first": 0.0, "recall_after": 0.0, "delta": 0.0,
                "retry_rate": 0.0, "give_up_rate": 0.0, "n": 0}
    r_first = r_after = retries = gave_up = 0.0
    for case in cases:
        cc, pack, hits = await first_pass_fn(case)
        r_first += recall(case.expected_symbols, hits)
        result = await correct_fn(case, cc, pack, hits)
        r_after += recall(case.expected_symbols, result.hits)
        retries += 1.0 if result.meta.get("retried") else 0.0
        gave_up += 1.0 if result.meta.get("gave_up") else 0.0
    return {
        "recall_first": r_first / n,
        "recall_after": r_after / n,
        "delta": (r_after - r_first) / n,
        "retry_rate": retries / n,
        "give_up_rate": gave_up / n,
        "n": n,
    }
