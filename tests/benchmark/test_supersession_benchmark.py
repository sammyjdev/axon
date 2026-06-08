"""Encodes the dec-115 merge criterion as a test.

Turning supersession on must improve ranking quality on the gold set without
introducing false positives or dropping any decision.
"""

from __future__ import annotations

from axon.benchmark.fixtures.supersession_gold import gold_scenarios
from axon.benchmark.supersession import run_ab


async def test_supersession_improves_ranking_without_false_positives() -> None:
    report = await run_ab(gold_scenarios())
    off, on = report["off"], report["on"]

    # Improvement: stale decisions are pushed well below their successor.
    assert on.current_precedence_rate == 1.0
    assert on.mean_stale_ratio < off.mean_stale_ratio
    assert on.mean_stale_ratio < 0.1

    # Guardrails: lossless and no collateral damage on controls.
    assert on.recall_completeness_rate == 1.0
    assert on.false_positive_rate == 0.0


async def test_gold_set_is_mixed() -> None:
    scenarios = gold_scenarios()
    assert any(s.is_control for s in scenarios)
    assert any(not s.is_control for s in scenarios)
