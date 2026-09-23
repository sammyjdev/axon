"""Tests for the TypeSafe supersession evaluation harness."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from math import inf, nextafter
from pathlib import Path

import pytest

from axon.benchmark.fixtures.supersession_gold import gold_scenarios
from axon.benchmark.supersession import (
    _LEXICAL_THRESHOLD,
    _FakeStore,
    lexical_similarity,
)
from axon.benchmark.typesafe import questions, supersession_eval
from axon.benchmark.typesafe.client import Answer
from axon.benchmark.typesafe.corpus import SupersessionCase
from axon.benchmark.typesafe.supersession_eval import (
    ConditionReport,
    Counts,
    detect_current,
    mcnemar_exact,
    run,
    weighted_precision,
    wilson_interval,
)
from axon.core.decision import Decision
from axon.recall.strategy import recall_context

_RANK_RE = re.compile(r"(dec-[\w.]+) \(rank ([\d.]+)\)")


async def _ranks(
    decisions,
    symbols,
    *,
    enable: bool,
    scope_threshold: float = questions.SCOPE_SIM_THRESHOLD,
    near_dup_threshold: float = questions.NEAR_DUP_THRESHOLD,
) -> dict[str, float]:
    output = await recall_context(
        "axon",
        symbols=list(symbols),
        store=_FakeStore(decisions),  # type: ignore[arg-type]
        token_budget=100_000,
        enable_supersession=enable,
        similarity=lexical_similarity if enable else None,
        similarity_threshold=scope_threshold,
        near_dup_threshold=near_dup_threshold,
    )
    return {match.group(1): float(match.group(2)) for match in _RANK_RE.finditer(output)}


async def test_detect_current_matches_production_for_every_gold_scenario() -> None:
    for scenario in gold_scenarios():
        older, newer = scenario.decisions
        baseline = await _ranks(scenario.decisions, scenario.query_symbols, enable=False)
        production = await _ranks(
            scenario.decisions,
            scenario.query_symbols,
            enable=True,
            scope_threshold=_LEXICAL_THRESHOLD,
            near_dup_threshold=questions.NEAR_DUP_THRESHOLD,
        )
        production_verdict = production[older.id] < baseline[older.id] * 0.5

        assert (
            detect_current(
                older,
                newer,
                older_status=older.status,
                similarity=lexical_similarity,
                scope_threshold=_LEXICAL_THRESHOLD,
                near_dup_threshold=questions.NEAR_DUP_THRESHOLD,
            )
            is production_verdict
        ), scenario.name


def _decision(
    number: int,
    summary: str,
    *,
    timestamp: datetime | None = None,
    status: str = "draft",
) -> Decision:
    return Decision(
        id=f"dec-{number:03d}",
        timestamp=timestamp or datetime(2026, 1, number, tzinfo=UTC),
        agent="manual",
        repo="axon",
        files=[Path("shared.py")],
        symbols=["Shared"],
        summary=summary,
        status=status,  # type: ignore[arg-type]
    )


def _case(
    case_id: str,
    older: Decision,
    newer: Decision,
    *,
    cosine: float,
    stratum: str = "mid",
    split: str = "holdout",
) -> SupersessionCase:
    return SupersessionCase(
        case_id=case_id,
        older_id=older.id,
        newer_id=newer.id,
        older_summary=older.summary,
        newer_summary=newer.summary,
        older_ts=older.timestamp.isoformat(),
        newer_ts=newer.timestamp.isoformat(),
        older_status=older.status,
        shared_scope=["shared.py"],
        cosine=cosine,
        stratum=stratum,
        split=split,
    )


def test_detect_current_honours_preexisting_superseded_status() -> None:
    older = _decision(1, "old behavior", status="superseded")
    newer = _decision(2, "unrelated addition")

    assert detect_current(
        older, newer, older_status=older.status, similarity=lambda left, right: 0.0
    )


async def test_detect_current_treats_first_decision_as_stale_on_timestamp_tie() -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    first = _decision(1, "use old backend for shared storage routing", timestamp=timestamp)
    second = _decision(
        2,
        "replace use old backend for shared storage routing",
        timestamp=timestamp,
    )
    decisions = (first, second)

    baseline = await _ranks(decisions, ("Shared",), enable=False)
    production = await _ranks(decisions, ("Shared",), enable=True)

    assert production[first.id] < baseline[first.id] * 0.5
    assert production[second.id] == baseline[second.id]
    assert detect_current(
        first, second, older_status=first.status, similarity=lambda left, right: 1.0
    )


def test_detect_current_rejects_revision_verb_below_scope_floor() -> None:
    older = _decision(1, "use old backend")
    newer = _decision(2, "replace old backend")

    assert not detect_current(
        older,
        newer,
        older_status=older.status,
        similarity=lambda left, right: questions.SCOPE_SIM_THRESHOLD - 0.001,
    )


def test_detect_current_accepts_revision_verb_between_thresholds() -> None:
    older = _decision(1, "use old backend")
    newer = _decision(2, "replace old backend")
    cosine = (questions.SCOPE_SIM_THRESHOLD + questions.NEAR_DUP_THRESHOLD) / 2

    assert detect_current(
        older, newer, older_status=older.status, similarity=lambda left, right: cosine
    )


def test_detect_current_accepts_near_duplicate_without_revision_verb() -> None:
    """The near-duplicate branch is what catches a reworded restatement.

    No gold scenario reaches 0.93 under the lexical proxy, so without this pair
    the whole `or cosine >= near_dup_threshold` disjunct could be deleted and
    every other test would still pass.
    """
    older = _decision(1, "the graph backend stores structural subgraphs")
    newer = _decision(2, "structural subgraphs are stored by the graph backend")

    assert detect_current(
        older,
        newer,
        older_status=older.status,
        similarity=lambda left, right: questions.NEAR_DUP_THRESHOLD,
    )
    assert not detect_current(
        older,
        newer,
        older_status=older.status,
        similarity=lambda left, right: questions.NEAR_DUP_THRESHOLD - 0.001,
    )


def test_wilson_interval_known_value_and_empty_trials() -> None:
    low, high = wilson_interval(8, 10)

    assert (round(low, 2), round(high, 2)) == (0.49, 0.94)
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_mcnemar_exact_uses_only_discordant_pairs() -> None:
    assert mcnemar_exact(5, 5) == 1.0
    assert mcnemar_exact(10, 0) < 0.01
    assert mcnemar_exact(0, 0) == 1.0


def _report(stratum: str, precision: float) -> ConditionReport:
    return ConditionReport(
        condition="current",
        stratum=stratum,
        counts=Counts(tp=0, fp=0, tn=0, fn=0),
        precision=precision,
        recall=0.0,
        f1=0.0,
        precision_ci=(0.0, 1.0),
        recall_ci=(0.0, 1.0),
    )


def test_weighted_precision_uses_natural_stratum_population() -> None:
    reports = [_report("common", 1.0), _report("rare", 0.0)]

    assert weighted_precision(reports, {"common": 9, "rare": 1}) == 0.9
    assert weighted_precision(reports, {"common": 1, "rare": 1}) == 0.5


async def test_run_keeps_tuning_cases_out_of_headline_metrics() -> None:
    tuning_old = _decision(1, "old tuning")
    tuning_new = _decision(2, "replace tuning")
    holdout_old = _decision(3, "old holdout")
    holdout_new = _decision(4, "replace holdout")
    cases = [
        _case("tuning", tuning_old, tuning_new, cosine=1.0, split="tuning"),
        _case("holdout", holdout_old, holdout_new, cosine=1.0),
    ]

    report = await run(
        cases=cases,
        labels={"tuning": False, "holdout": True},
        decisions={decision.id: decision for decision in (
            tuning_old,
            tuning_new,
            holdout_old,
            holdout_new,
        )},
        strata_population={"mid": 2},
        client=None,
        similarity=lambda left, right: 1.0,
    )

    assert report["conditions"]["current"]["aggregate"]["counts"] == {
        "tp": 1,
        "fp": 0,
        "tn": 0,
        "fn": 0,
    }
    assert report["tuning"]["current"]["counts"] == {
        "tp": 0,
        "fp": 1,
        "tn": 0,
        "fn": 0,
    }


async def test_verb_only_keeps_scope_floor() -> None:
    older = _decision(1, "use old backend")
    newer = _decision(2, "replace old backend")
    case = _case(
        "below-floor",
        older,
        newer,
        cosine=questions.SCOPE_SIM_THRESHOLD - 0.001,
        stratum="low",
    )

    report = await run(
        cases=[case],
        labels={case.case_id: False},
        decisions={older.id: older, newer.id: newer},
        strata_population={"low": 1},
        client=None,
        similarity=lambda left, right: case.cosine,
    )

    assert report["conditions"]["verb_only"]["aggregate"]["counts"] == {
        "tp": 0,
        "fp": 0,
        "tn": 1,
        "fn": 0,
    }


class _StubClient:
    def __init__(self, probabilities: dict[str, float | list[float]]) -> None:
        self.probabilities = probabilities
        self.calls: list[tuple[str, int]] = []

    async def ask(self, state: dict, question_map: dict, *, sample_index: int = 0) -> Answer:
        case_id = state["case_id"]
        self.calls.append((case_id, sample_index))
        assert set(question_map) == {"supersession"}
        configured = self.probabilities[case_id]
        probability = (
            configured[sample_index] if isinstance(configured, list) else configured
        )
        return Answer(
            payload={
                "supersession": {
                    "type": "noul",
                    "noul": probability,
                }
            },
            model="jev-test",
            input_tokens=10,
            output_tokens=1,
            latency_s=0.25,
            cost_usd=0.01,
            cached=False,
        )


async def test_typesafe_uses_client_probability_and_half_is_positive() -> None:
    positive_old = _decision(1, "positive old")
    positive_new = _decision(2, "positive new")
    negative_old = _decision(3, "negative old")
    negative_new = _decision(4, "negative new")
    cases = [
        _case("at-half", positive_old, positive_new, cosine=0.0, stratum="low"),
        _case("below-half", negative_old, negative_new, cosine=0.0, stratum="low"),
    ]
    stub = _StubClient({"at-half": 0.5, "below-half": 0.4999})

    report = await run(
        cases=cases,
        labels={"at-half": True, "below-half": False},
        decisions={decision.id: decision for decision in (
            positive_old,
            positive_new,
            negative_old,
            negative_new,
        )},
        strata_population={"low": 2},
        client=stub,  # type: ignore[arg-type]
        similarity=lambda left, right: 0.0,
        samples=1,
    )

    typesafe = report["conditions"]["typesafe"]
    assert typesafe["aggregate"]["counts"] == {"tp": 1, "fp": 0, "tn": 1, "fn": 0}
    assert typesafe["cost_usd"] == 0.02
    assert typesafe["latency_s"] == 0.5
    assert report["model"] == "jev-test"
    assert stub.calls == [("at-half", 0), ("below-half", 0)]


async def test_typesafe_headline_uses_one_call_not_repeat_ensemble() -> None:
    older = _decision(1, "old")
    newer = _decision(2, "new")
    case = _case("case", older, newer, cosine=0.0, stratum="low")

    report = await run(
        cases=[case],
        labels={case.case_id: True},
        decisions={older.id: older, newer.id: newer},
        strata_population={"low": 1},
        client=_StubClient({"case": [0.4, 0.9]}),  # type: ignore[arg-type]
        similarity=lambda left, right: 0.0,
        samples=2,
    )

    assert report["conditions"]["typesafe"]["aggregate"]["counts"]["fn"] == 1
    assert report["conditions"]["typesafe"]["repeat_variance"] > 0.0


async def test_client_none_skips_typesafe_and_reports_deterministic_arms() -> None:
    older = _decision(1, "old")
    newer = _decision(2, "new")
    case = _case("case", older, newer, cosine=0.0, stratum="low")

    report = await run(
        cases=[case],
        labels={case.case_id: False},
        decisions={older.id: older, newer.id: newer},
        strata_population={"low": 1},
        client=None,
        similarity=lambda left, right: 0.0,
    )

    assert set(report["conditions"]) == {
        "current",
        "current_recalibrated",
        "verb_only",
    }
    assert all(
        condition["repeat_variance"] == 0.0
        for condition in report["conditions"].values()
    )
    assert report["model"] is None
    json.dumps(report)


async def test_recalibration_can_choose_no_positives() -> None:
    older = _decision(1, "old backend")
    newer = _decision(2, "replace old backend")
    case = _case(
        "negative",
        older,
        newer,
        cosine=1.0,
        stratum="high",
        split="tuning",
    )

    report = await run(
        cases=[case],
        labels={case.case_id: False},
        decisions={older.id: older, newer.id: newer},
        strata_population={"high": 1},
        client=None,
        similarity=lambda left, right: 1.0,
    )

    assert report["tuning"]["current_recalibrated"]["counts"]["tn"] == 1
    assert report["recalibrated_thresholds"]["scope_threshold"] > 1.0


async def test_deterministic_arms_report_measured_similarity_latency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    older = _decision(1, "old")
    newer = _decision(2, "new")
    case = _case("case", older, newer, cosine=0.0, stratum="low")
    clock = iter((10.0, 12.5))
    monkeypatch.setattr(supersession_eval, "perf_counter", lambda: next(clock))

    report = await run(
        cases=[case],
        labels={case.case_id: False},
        decisions={older.id: older, newer.id: newer},
        strata_population={"low": 1},
        client=None,
        similarity=lambda left, right: 0.0,
    )

    for condition in ("current", "current_recalibrated", "verb_only"):
        measured = report["conditions"][condition]
        assert measured["latency_s"] == 2.5
        assert measured["cost_usd"] is None
        assert measured["cost_measured"] is False


async def test_typesafe_threshold_curve_uses_tuning_probabilities_only() -> None:
    tuning_old = _decision(1, "tuning old")
    tuning_new = _decision(2, "tuning new")
    holdout_old = _decision(3, "holdout old")
    holdout_new = _decision(4, "holdout new")
    cases = [
        _case(
            "tuning",
            tuning_old,
            tuning_new,
            cosine=0.0,
            stratum="low",
            split="tuning",
        ),
        _case("holdout", holdout_old, holdout_new, cosine=0.0, stratum="low"),
    ]

    report = await run(
        cases=cases,
        labels={"tuning": False, "holdout": True},
        decisions={decision.id: decision for decision in (
            tuning_old,
            tuning_new,
            holdout_old,
            holdout_new,
        )},
        strata_population={"low": 2},
        client=_StubClient({"tuning": 0.25, "holdout": 0.75}),  # type: ignore[arg-type]
        similarity=lambda left, right: 0.0,
        samples=1,
    )

    assert {
        point["threshold"] for point in report["tuning"]["typesafe_threshold_curve"]
    } == {0.0, 0.25, 0.5, 1.0, nextafter(1.0, inf)}
    assert report["conditions"]["typesafe"]["human_review_fraction"] == 0.0


def test_detect_current_reads_the_pinned_status_not_the_live_decision() -> None:
    """The corpus pins the status, so the arm's prediction cannot drift.

    Review finding 1 (2026-09-22): the arm short-circuited on the live
    ``Decision.status``, which production mutates through ``_mark_superseded``.
    Rerunning the same pinned corpus a week later produced different numbers,
    and the oracle was partly the output of the detector under test. The status
    now enters as an explicit argument, like every other pinned input.
    """
    older = _decision(1, "old behavior", status="superseded")
    newer = _decision(2, "unrelated addition")

    assert not detect_current(
        older, newer, older_status="active", similarity=lambda left, right: 0.0
    )
    assert detect_current(
        older, newer, older_status="superseded", similarity=lambda left, right: 0.0
    )

