"""Evaluate supersession detectors against human-labelled decision pairs.

The current arm exactly mirrors the pair-level production detector and is
guarded by a fidelity test through ``recall_context``. The evaluation measures
detector quality on an extracted corpus, not production candidate retrieval,
which still limits comparisons to decisions found by recall's repo and symbol
queries.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from math import comb, inf, nextafter, sqrt
from statistics import pvariance
from time import perf_counter
from typing import Any

from axon.benchmark.typesafe import questions
from axon.benchmark.typesafe.client import TypeSafeClient
from axon.benchmark.typesafe.corpus import SupersessionCase
from axon.core.decision import Decision
from axon.recall.strategy import _scope
from axon.recall.supersession import PairwiseSimilarity, has_revision_verb

CONDITIONS = ("current", "current_recalibrated", "verb_only", "typesafe")


@dataclass(frozen=True)
class Counts:
    tp: int
    fp: int
    tn: int
    fn: int


@dataclass(frozen=True)
class ConditionReport:
    condition: str
    stratum: str | None
    counts: Counts
    precision: float | None
    recall: float | None
    f1: float | None
    precision_ci: tuple[float, float] | None
    recall_ci: tuple[float, float] | None


def detect_current(
    older: Decision,
    newer: Decision,
    *,
    older_status: str,
    similarity: PairwiseSimilarity,
    scope_threshold: float = questions.SCOPE_SIM_THRESHOLD,
    near_dup_threshold: float = questions.NEAR_DUP_THRESHOLD,
) -> bool:
    """Exact replica of the pair-level decision inside ``_mark_superseded``.

    Returns True when production would mark ``older`` superseded.

    ``older_status`` is passed in rather than read off ``older`` on purpose.
    Production mutates that field through ``_mark_superseded``, so reading it
    live made the arm's prediction depend on when the eval ran, and made the
    oracle partly the output of the detector under test. The corpus pins it at
    extraction, like every other input here.
    """
    if older_status == "superseded":
        return True
    if not (_scope(older) & _scope(newer)):
        return False
    cosine = similarity(older.summary, newer.summary)
    if cosine < scope_threshold or older.timestamp > newer.timestamp:
        return False
    return has_revision_verb(newer.summary) or cosine >= near_dup_threshold


def wilson_interval(
    successes: int,
    trials: int,
    z: float = 1.96,
) -> tuple[float, float] | None:
    """Return the Wilson score interval.

    Zero trials are undefined. That is not precision 0 and not the interval
    (0, 1): a detector that never fires has no precision to compare, and the
    holdout gate is inconclusive on that metric.
    """
    if trials == 0:
        return None
    probability = successes / trials
    denominator = 1 + z**2 / trials
    centre = (probability + z**2 / (2 * trials)) / denominator
    half = (
        z
        * sqrt(
            probability * (1 - probability) / trials
            + z**2 / (4 * trials**2)
        )
        / denominator
    )
    return centre - half, centre + half


def mcnemar_exact(only_a: int, only_b: int) -> float:
    """Return the two-sided exact McNemar p-value for discordant pairs."""
    discordant = only_a + only_b
    if discordant == 0:
        return 1.0
    tail = sum(comb(discordant, value) for value in range(min(only_a, only_b) + 1))
    return min(1.0, 2 * tail / 2**discordant)


def weighted_precision(
    per_stratum: Sequence[ConditionReport],
    population: Mapping[str, int],
) -> float | None:
    """Weight stratum precision by its natural population share.

    Undefined stratum precision makes the weighted figure undefined too. A
    detector that never fires in a stratum does not contribute a precision of 0.
    """
    reports: dict[str, float] = {}
    for report in per_stratum:
        if report.stratum is None:
            continue
        if report.precision is None:
            return None
        reports[report.stratum] = report.precision
    total = sum(population.get(stratum, 0) for stratum in reports)
    if total == 0:
        return None
    return sum(
        reports[stratum] * population.get(stratum, 0) for stratum in reports
    ) / total


def _condition_report(
    condition: str,
    stratum: str | None,
    cases: Sequence[SupersessionCase],
    labels: Mapping[str, bool],
    predictions: Mapping[str, bool],
) -> ConditionReport:
    selected = [case for case in cases if stratum is None or case.stratum == stratum]
    tp = sum(labels[case.case_id] and predictions[case.case_id] for case in selected)
    fp = sum(not labels[case.case_id] and predictions[case.case_id] for case in selected)
    tn = sum(not labels[case.case_id] and not predictions[case.case_id] for case in selected)
    fn = sum(labels[case.case_id] and not predictions[case.case_id] for case in selected)
    predicted_positive = tp + fp
    labelled_positive = tp + fn
    precision = tp / predicted_positive if predicted_positive else None
    recall = tp / labelled_positive if labelled_positive else None
    if precision is None or recall is None:
        f1 = None
    elif precision + recall == 0:
        f1 = 0.0
    else:
        f1 = 2 * precision * recall / (precision + recall)
    return ConditionReport(
        condition=condition,
        stratum=stratum,
        counts=Counts(tp=tp, fp=fp, tn=tn, fn=fn),
        precision=precision,
        recall=recall,
        f1=f1,
        precision_ci=wilson_interval(tp, predicted_positive),
        recall_ci=wilson_interval(tp, labelled_positive),
    )


def _predict_current(
    cases: Sequence[SupersessionCase],
    decisions: Mapping[str, Decision],
    cosines: Mapping[str, float],
    *,
    scope_threshold: float,
    near_dup_threshold: float,
) -> dict[str, bool]:
    def fixed(score: float) -> PairwiseSimilarity:
        """The cosine for this pair was measured once, up front; hand it back."""

        def similarity(left: str, right: str) -> float:
            return score

        return similarity

    return {
        case.case_id: detect_current(
            decisions[case.older_id],
            decisions[case.newer_id],
            older_status=case.older_status,
            similarity=fixed(cosines[case.case_id]),
            scope_threshold=scope_threshold,
            near_dup_threshold=near_dup_threshold,
        )
        for case in cases
    }


def _choose_thresholds(
    tuning: Sequence[SupersessionCase],
    labels: Mapping[str, bool],
    decisions: Mapping[str, Decision],
    cosines: Mapping[str, float],
) -> tuple[float, float]:
    if not tuning:
        return questions.SCOPE_SIM_THRESHOLD, questions.NEAR_DUP_THRESHOLD
    candidate_values = {
        questions.SCOPE_SIM_THRESHOLD,
        questions.NEAR_DUP_THRESHOLD,
        *(cosines[case.case_id] for case in tuning),
    }
    candidate_values.add(nextafter(max(candidate_values), inf))
    candidates = sorted(candidate_values)
    best = (questions.SCOPE_SIM_THRESHOLD, questions.NEAR_DUP_THRESHOLD)
    best_key: tuple[float, float, float, float] | None = None
    for scope_threshold in candidates:
        for near_dup_threshold in candidates:
            if near_dup_threshold < scope_threshold:
                continue
            predictions = _predict_current(
                tuning,
                decisions,
                cosines,
                scope_threshold=scope_threshold,
                near_dup_threshold=near_dup_threshold,
            )
            report = _condition_report(
                "current_recalibrated", None, tuning, labels, predictions
            )
            correct = report.counts.tp + report.counts.tn
            distance = abs(scope_threshold - questions.SCOPE_SIM_THRESHOLD) + abs(
                near_dup_threshold - questions.NEAR_DUP_THRESHOLD
            )
            # The published metric stays undefined. The search still needs a
            # number so accuracy can break a tie between "never fired" and a
            # false positive. Undefined sorts as 0, matching that tie.
            key = (
                0.0 if report.f1 is None else report.f1,
                0.0 if report.precision is None else report.precision,
                correct / len(tuning),
                -distance,
            )
            if best_key is None or key > best_key:
                best_key = key
                best = scope_threshold, near_dup_threshold
    return best


def _reports(
    condition: str,
    cases: Sequence[SupersessionCase],
    labels: Mapping[str, bool],
    predictions: Mapping[str, bool],
    strata_population: Mapping[str, int],
) -> tuple[ConditionReport, dict[str, ConditionReport]]:
    aggregate = _condition_report(condition, None, cases, labels, predictions)
    strata = {
        stratum: _condition_report(condition, stratum, cases, labels, predictions)
        for stratum in sorted(
            set(strata_population) | {case.stratum for case in cases}
        )
    }
    return aggregate, strata


def _mcnemar_against_current(
    cases: Sequence[SupersessionCase],
    labels: Mapping[str, bool],
    current: Mapping[str, bool],
    other: Mapping[str, bool],
) -> float:
    only_current = sum(
        current[case.case_id] == labels[case.case_id]
        and other[case.case_id] != labels[case.case_id]
        for case in cases
    )
    only_other = sum(
        current[case.case_id] != labels[case.case_id]
        and other[case.case_id] == labels[case.case_id]
        for case in cases
    )
    return mcnemar_exact(only_current, only_other)


async def run(
    *,
    cases: Sequence[SupersessionCase],
    labels: Mapping[str, bool],
    decisions: Mapping[str, Decision],
    strata_population: Mapping[str, int],
    client: TypeSafeClient | None,
    similarity: PairwiseSimilarity,
    samples: int = 5,
) -> dict:
    """Run every available condition and return holdout and tuning reports."""
    tuning = [case for case in cases if case.split == "tuning"]
    holdout = [case for case in cases if case.split == "holdout"]
    similarity_started = perf_counter()
    cosines = {
        case.case_id: similarity(case.older_summary, case.newer_summary)
        for case in cases
    }
    similarity_latency = perf_counter() - similarity_started
    scope_threshold, near_dup_threshold = _choose_thresholds(
        tuning, labels, decisions, cosines
    )
    predictions: dict[str, dict[str, bool]] = {
        "current": _predict_current(
            cases,
            decisions,
            cosines,
            scope_threshold=questions.SCOPE_SIM_THRESHOLD,
            near_dup_threshold=questions.NEAR_DUP_THRESHOLD,
        ),
        "current_recalibrated": _predict_current(
            cases,
            decisions,
            cosines,
            scope_threshold=scope_threshold,
            near_dup_threshold=near_dup_threshold,
        ),
        "verb_only": {
            case.case_id: has_revision_verb(case.newer_summary)
            and cosines[case.case_id] >= questions.SCOPE_SIM_THRESHOLD
            for case in cases
        },
    }
    costs: dict[str, float | None] = {
        condition: None for condition in CONDITIONS
    }
    costs["typesafe"] = 0.0
    latencies = {
        condition: similarity_latency for condition in CONDITIONS
    }
    latencies["typesafe"] = 0.0
    variances = {condition: 0.0 for condition in CONDITIONS}
    models: set[str] = set()
    typesafe_samples: list[dict[str, bool]] = []
    primary_probabilities: dict[str, float] = {}

    if client is not None:
        probabilities: dict[str, list[float]] = {case.case_id: [] for case in cases}
        for sample_index in range(samples):
            sample_predictions: dict[str, bool] = {}
            for case in cases:
                answer = await client.ask(
                    {
                        "case_id": case.case_id,
                        "older_decision": case.older_summary,
                        "newer_decision": case.newer_summary,
                        "shared_scope": case.shared_scope,
                    },
                    {
                        "supersession": questions.supersession_question(
                            case.older_summary, case.newer_summary
                        )
                    },
                    sample_index=sample_index,
                )
                probability = float(answer.payload["supersession"]["noul"])
                probabilities[case.case_id].append(probability)
                sample_predictions[case.case_id] = (
                    probability >= questions.NOUL_PRIMARY_THRESHOLD
                )
                costs["typesafe"] = (costs["typesafe"] or 0.0) + answer.cost_usd
                latencies["typesafe"] += answer.latency_s
                models.add(answer.model)
            typesafe_samples.append(sample_predictions)
        primary_probabilities = {
            case_id: values[0] for case_id, values in probabilities.items()
        }
        predictions["typesafe"] = {
            case_id: probability >= questions.NOUL_PRIMARY_THRESHOLD
            for case_id, probability in primary_probabilities.items()
        }
        # Spread across repeats. An undefined F1 counts as 0 here only, so a
        # repeat that never fires still disagrees with a repeat that hits.
        # The reported precision and recall stay undefined.
        repeat_f1 = [
            0.0 if f1 is None else f1
            for f1 in (
                _condition_report("typesafe", None, holdout, labels, sample).f1
                for sample in typesafe_samples
            )
        ]
        variances["typesafe"] = pvariance(repeat_f1) if repeat_f1 else 0.0

    condition_reports: dict[str, dict] = {}
    # Kept apart from the threshold curve: mixing a report with a list of points
    # under one dict forces an `object` type that hides real mistakes.
    tuning_reports: dict[str, ConditionReport] = {}
    threshold_curve: list[dict[str, Any]] = []
    for condition, condition_predictions in predictions.items():
        aggregate, strata = _reports(
            condition,
            holdout,
            labels,
            condition_predictions,
            strata_population,
        )
        condition_reports[condition] = {
            "aggregate": aggregate,
            "strata": strata,
            "weighted_precision": weighted_precision(
                list(strata.values()), strata_population
            ),
            "repeat_variance": variances[condition],
            "cost_usd": costs[condition],
            "cost_measured": condition == "typesafe",
            "latency_s": latencies[condition],
            "measurement_cases": len(cases),
        }
        tuning_reports[condition] = _condition_report(
            condition, None, tuning, labels, condition_predictions
        )

    if client is not None:
        condition_reports["typesafe"]["human_review_fraction"] = (
            sum(
                questions.NOUL_UNCERTAIN_LOW
                <= primary_probabilities[case.case_id]
                <= questions.NOUL_UNCERTAIN_HIGH
                for case in holdout
            )
            / len(holdout)
            if holdout
            else 0.0
        )
        threshold_values = {
            0.0,
            questions.NOUL_PRIMARY_THRESHOLD,
            1.0,
            *(primary_probabilities[case.case_id] for case in tuning),
        }
        threshold_values.add(nextafter(max(threshold_values), inf))
        thresholds = sorted(threshold_values)
        threshold_curve = [
            {
                "threshold": threshold,
                "report": _condition_report(
                    "typesafe",
                    None,
                    tuning,
                    labels,
                    {
                        case_id: probability >= threshold
                        for case_id, probability in primary_probabilities.items()
                    },
                ),
            }
            for threshold in thresholds
        ]

    current_predictions = predictions["current"]
    serialised_conditions = {
        condition: {
            **values,
            "aggregate": asdict(values["aggregate"]),
            "strata": {
                stratum: asdict(report)
                for stratum, report in values["strata"].items()
            },
        }
        for condition, values in condition_reports.items()
    }
    serialised_tuning: dict[str, Any] = {
        condition: asdict(report) for condition, report in tuning_reports.items()
    }
    if client is not None:
        serialised_tuning["typesafe_threshold_curve"] = [
            {"threshold": point["threshold"], "report": asdict(point["report"])}
            for point in threshold_curve
        ]
    return {
        "conditions": serialised_conditions,
        "tuning": serialised_tuning,
        "mcnemar_vs_current": {
            condition: _mcnemar_against_current(
                holdout,
                labels,
                current_predictions,
                condition_predictions,
            )
            for condition, condition_predictions in predictions.items()
            if condition != "current"
        },
        "recalibrated_thresholds": {
            "scope_threshold": scope_threshold,
            "near_dup_threshold": near_dup_threshold,
        },
        "embedder": questions.EMBEDDER_MODEL,
        "model": next(iter(models)) if len(models) == 1 else sorted(models) or None,
        "cost_note": (
            "Embedding cost is unavailable through PairwiseSimilarity; deterministic "
            "arm cost_usd values are null rather than assumed zero."
        ),
    }
