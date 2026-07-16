from __future__ import annotations

import pytest

from axon.benchmark.retrieval_eval import (
    GoldenCase,
    evaluate,
    load_golden,
    precision,
    recall,
    symbols_of,
)
from axon.retrieval.self_correct import CorrectionResult


def test_symbols_of_extracts_payload_symbols():
    assert symbols_of([{"payload": {"symbol": "A"}}, {"payload": {"symbol": "B"}}]) == {"A", "B"}


def test_recall_full_and_partial():
    hits_full = [{"payload": {"symbol": "A"}}, {"payload": {"symbol": "B"}}]
    assert recall(frozenset({"A", "B"}), hits_full) == 1.0
    assert recall(frozenset({"A", "B"}), [{"payload": {"symbol": "A"}}]) == 0.5
    assert recall(frozenset(), []) == 1.0  # nothing expected -> trivially satisfied


def test_precision_full_partial_and_empty():
    hits_full = [{"payload": {"symbol": "A"}}, {"payload": {"symbol": "B"}}]
    assert precision(frozenset({"A", "B"}), hits_full) == 1.0
    assert precision(frozenset({"A"}), hits_full) == 0.5
    assert precision(frozenset({"A"}), []) == 1.0


def test_load_golden_reads_fixture(tmp_path):
    p = tmp_path / "g.json"
    p.write_text('[{"query": "q", "ctx": "personal", "expected_symbols": ["A"]}]')
    cases = load_golden(str(p))
    assert cases == [GoldenCase("q", "personal", frozenset({"A"}))]


@pytest.mark.asyncio
async def test_evaluate_computes_delta_and_rates():
    cases = [GoldenCase("q1", "personal", frozenset({"A"}))]

    async def first_pass(case):
        return ("CTX", object(), [{"score": 0.05, "payload": {"symbol": "Z"}}])

    async def correct(case, cc, pack, hits):
        return CorrectionResult("CTX2", pack, [{"payload": {"symbol": "A"}}],
                                {"retried": True, "gave_up": False,
                                 "strategy_used": "reformulate", "verdict": "low_score"})

    report = await evaluate(cases, first_pass, correct)
    assert report["recall_first"] == 0.0
    assert report["recall_after"] == 1.0
    assert report["delta"] == 1.0
    assert report["retry_rate"] == 1.0
    assert report["give_up_rate"] == 0.0
    assert report["n"] == 1


@pytest.mark.asyncio
async def test_evaluate_computes_precision_means():
    cases = [GoldenCase("q1", "personal", frozenset({"A"}))]

    async def first_pass(case):
        return ("CTX", object(), [{"payload": {"symbol": "A"}},
                                   {"payload": {"symbol": "Z"}}])

    async def correct(case, cc, pack, hits):
        return CorrectionResult("CTX2", pack, [{"payload": {"symbol": "A"}}], {})

    report = await evaluate(cases, first_pass, correct)
    assert report["precision_first"] == 0.5
    assert report["precision_after"] == 1.0
