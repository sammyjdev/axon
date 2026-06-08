"""A/B benchmark for soft supersession in recall (dec-115).

Runs the mixed gold set (``fixtures/supersession_gold.py``) through
``recall_context`` twice — flag OFF (baseline) and flag ON — and reports whether
turning it on:

- pushes stale decisions below their successor (``current_precedence`` ↑,
  ``stale_ratio`` ↓), while
- keeping them in the result (``recall_completeness`` = 100%), and
- never demoting unrelated decisions in control scenarios
  (``false_positive`` = 0%).

It is fully deterministic and offline. Production uses embedding cosine; this
benchmark substitutes a lexical Jaccard proxy so it needs no model download and
its results are reproducible — the *mechanism* under test (scope + semantic
agreement → ranking penalty) is identical either way.

Run it with::

    python -m axon.benchmark.supersession
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from axon.benchmark.fixtures.supersession_gold import (
    SupersessionScenario,
    gold_scenarios,
)
from axon.core.decision import Decision

# Jaccard operates on a different scale than embedding cosine, so the benchmark
# uses its own threshold. Authored gold summaries put real supersession pairs
# above it and control pairs below it.
_LEXICAL_THRESHOLD = 0.40
# A control decision is judged "wrongly demoted" if the flag halves its rank or
# worse — the 0.02x penalty would crush it far below this, so the test is robust.
_FALSE_POSITIVE_DROP = 0.5

_RANK_RE = re.compile(r"(dec-[\w.]+) \(rank ([\d.]+)\)")


def lexical_similarity(left: str, right: str) -> float:
    wl = set(re.findall(r"[a-z0-9]+", left.lower()))
    wr = set(re.findall(r"[a-z0-9]+", right.lower()))
    if not wl or not wr:
        return 0.0
    return len(wl & wr) / len(wl | wr)


class _FakeStore:
    """In-memory store exposing only what recall_context reads."""

    def __init__(self, decisions: tuple[Decision, ...]) -> None:
        self._decisions = list(decisions)

    async def find_decisions_by_repo(self, repo: str, limit: int = 20) -> list[Decision]:
        return [d for d in self._decisions if d.repo == repo][:limit]

    async def find_decisions_by_symbol(self, symbol_id: str) -> list[Decision]:
        return [d for d in self._decisions if symbol_id in d.symbols]


async def _ranks(scenario: SupersessionScenario, *, enable: bool) -> dict[str, float]:
    from axon.recall.strategy import recall_context

    out = await recall_context(
        "axon",
        symbols=list(scenario.query_symbols),
        store=_FakeStore(scenario.decisions),  # type: ignore[arg-type]
        token_budget=100_000,
        enable_supersession=enable,
        similarity=lexical_similarity if enable else None,
        similarity_threshold=_LEXICAL_THRESHOLD,
        # This benchmark exercises the ranking *mechanism* with a lexical proxy,
        # not the production embedding detector. Treat any pair above the lexical
        # floor as a near-duplicate so the gold pairs are detected without
        # depending on revision-verb wording; detection quality is validated
        # separately against real embeddings.
        near_dup_threshold=_LEXICAL_THRESHOLD,
    )
    return {m.group(1): float(m.group(2)) for m in _RANK_RE.finditer(out)}


@dataclass(frozen=True)
class Metrics:
    current_precedence_rate: float  # ↑ current ranked above stale
    mean_stale_ratio: float  # ↓ rank[stale] / rank[current]
    recall_completeness_rate: float  # = 1.0 stale never dropped
    false_positive_rate: float  # = 0.0 no control wrongly demoted
    n_supersession: int
    n_control: int


async def run_ab(scenarios: tuple[SupersessionScenario, ...]) -> dict[str, Metrics]:
    report: dict[str, Metrics] = {}
    for mode, enable in (("off", False), ("on", True)):
        precedence_hits = 0
        completeness_hits = 0
        ratios: list[float] = []
        supersession = [s for s in scenarios if not s.is_control]
        controls = [s for s in scenarios if s.is_control]

        for sc in supersession:
            ranks = await _ranks(sc, enable=enable)
            assert sc.current_id and sc.stale_id
            cur = ranks.get(sc.current_id, 0.0)
            stale = ranks.get(sc.stale_id, 0.0)
            if sc.stale_id in ranks:
                completeness_hits += 1
            if cur > stale:
                precedence_hits += 1
            ratios.append(stale / cur if cur > 0 else 1.0)

        false_positives = 0
        for sc in controls:
            off = await _ranks(sc, enable=False)
            on = await _ranks(sc, enable=enable)
            for dec_id, off_rank in off.items():
                if off_rank > 0 and on.get(dec_id, 0.0) < _FALSE_POSITIVE_DROP * off_rank:
                    false_positives += 1
                    break

        n_sup = len(supersession) or 1
        n_ctrl = len(controls) or 1
        report[mode] = Metrics(
            current_precedence_rate=precedence_hits / n_sup,
            mean_stale_ratio=sum(ratios) / len(ratios) if ratios else 0.0,
            recall_completeness_rate=completeness_hits / n_sup,
            false_positive_rate=false_positives / n_ctrl,
            n_supersession=len(supersession),
            n_control=len(controls),
        )
    return report


def format_report(report: dict[str, Metrics]) -> str:
    off, on = report["off"], report["on"]
    lines = [
        "## AXON supersession benchmark (dec-115)",
        f"scenarios: {on.n_supersession} supersession, {on.n_control} control",
        "",
        f"{'metric':<26}{'baseline (off)':>16}{'supersession (on)':>20}",
        f"{'-' * 62}",
        f"{'current_precedence':<26}{off.current_precedence_rate:>15.0%}"
        f"{on.current_precedence_rate:>20.0%}",
        f"{'mean_stale_ratio (↓)':<26}{off.mean_stale_ratio:>16.3f}"
        f"{on.mean_stale_ratio:>20.3f}",
        f"{'recall_completeness':<26}{off.recall_completeness_rate:>15.0%}"
        f"{on.recall_completeness_rate:>20.0%}",
        f"{'false_positive':<26}{off.false_positive_rate:>15.0%}"
        f"{on.false_positive_rate:>20.0%}",
    ]
    return "\n".join(lines)


async def main() -> None:
    report = await run_ab(gold_scenarios())
    print(format_report(report))


if __name__ == "__main__":
    asyncio.run(main())
