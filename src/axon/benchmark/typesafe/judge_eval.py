"""Measured judge arms for the TypeSafe pilot."""

from __future__ import annotations

import inspect
import logging
import re
import statistics
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from axon.benchmark.typesafe import questions
from axon.benchmark.typesafe.client import JsonCache, TypeSafeClient, cache_key
from axon.benchmark.typesafe.corpus import JudgeCase
from axon.benchmark.typesafe.supersession_eval import wilson_interval
from axon.core.decision import Decision
from axon.router.engine import TaskRequest, complete_with_usage
from axon.validation.judge import _parse_score
from axon.validation.prompts import build_judge_prompt

logger = logging.getLogger(__name__)

# One attempt's outcome: the score, and the model that produced it.
_Scored = tuple[float | None, str | None]

ARMS = ("llm_current", "llm_decomposed", "typesafe_single", "typesafe_composite")
DEFAULT_BAND_EDGES = (2.5, 3.5)
_CANDIDATE_EDGES = tuple(index / 2 for index in range(1, 10))
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
# complete() is a compat wrapper that discards usage, and usage is where the
# router records which model actually answered. The arms go through
# complete_with_usage instead: same routing, same call, nothing thrown away.
_complete_with_usage = complete_with_usage


@dataclass(frozen=True)
class ArmReport:
    arm: str
    band_agreement: float
    agreement_ci: tuple[float, float]
    mean_item_stdev: float
    max_item_stdev: float
    parse_failure_rate: float
    n_items: int
    n_attempts: int
    cost_usd: float
    latency_s_mean: float
    model_reported: str | None
    sampling: dict[str, Any]


def to_band(score: float, edges: tuple[float, float]) -> str:
    low, high = edges
    if score < low:
        return "weak"
    if score < high:
        return "ok"
    return "strong"


def fit_band_edges(scores: Mapping[str, float], labels: Mapping[str, str]) -> tuple[float, float]:
    """Choose deterministic cuts that maximize tuning band agreement."""
    candidates = [
        (low, high) for low in _CANDIDATE_EDGES for high in _CANDIDATE_EDGES if low < high
    ]
    if not scores:
        return DEFAULT_BAND_EDGES

    def rank(edges: tuple[float, float]) -> tuple[float, float, tuple[float, ...]]:
        agreement = sum(
            to_band(score, edges) == labels.get(case_id) for case_id, score in scores.items()
        )
        distance = (edges[0] - DEFAULT_BAND_EDGES[0]) ** 2 + (edges[1] - DEFAULT_BAND_EDGES[1]) ** 2
        return agreement / len(scores), -distance, tuple(-edge for edge in edges)

    return max(candidates, key=rank)


def item_stdevs(scores: Mapping[str, Sequence[float]]) -> tuple[float, float]:
    """Return mean and max population stdev, excluding items without scores."""
    values = [statistics.pstdev(repeats) for repeats in scores.values() if repeats]
    return (statistics.fmean(values), max(values)) if values else (0.0, 0.0)


def _provider_of(models: set[str], arm: str) -> str | None:
    """The provider prefix of the model that answered, when the arms agree on one.

    litellm model ids carry the provider first ("openrouter/anthropic/claude-haiku-4"),
    which is the only provider signal the router hands back.
    """
    if arm.startswith("typesafe"):
        return "typesafe"
    prefixes = {model.split("/")[0] for model in models if model}
    return prefixes.pop() if len(prefixes) == 1 else None


def _state(case: JudgeCase) -> dict[str, Any]:
    return {
        "id": case.case_id,
        "summary": case.summary,
        "repo": case.repo,
        "files": list(case.files),
        "symbols": list(case.symbols),
    }


def _decision(case: JudgeCase) -> Decision:
    return Decision(
        id=case.case_id,
        timestamp=datetime.now(UTC),
        agent="codex",
        repo=case.repo,
        files=[Path(path) for path in case.files],
        symbols=list(case.symbols),
        summary=case.summary,
    )


async def _resolve(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _cached_llm(
    *,
    cache: JsonCache,
    state: dict[str, Any],
    arm: str,
    sample_index: int,
    request: dict[str, Any],
    call: Callable[[], Awaitable[_Scored] | _Scored],
) -> tuple[float | None, float, float, str | None]:
    key = cache_key(
        state=state,
        question_map=request,
        model=f"router:{arm}",
        sample_index=sample_index,
    )
    cached = cache.get(key)
    if cached is not None:
        return cached["score"], 0.0, 0.0, cached.get("model")
    started = time.perf_counter()
    score, model = await _resolve(call())
    latency_s = time.perf_counter() - started
    cache.set(key, {"score": score, "model": model})
    return score, latency_s, 0.0, model


def _parse_decomposed(raw: str) -> dict[str, float] | None:
    values = [float(value) for value in _NUMBER_RE.findall(raw)]
    names = list(questions.JUDGE_COMPOSITE_LEVELS)
    if len(values) != len(names) or any(value < 0.0 or value > 2.0 for value in values):
        return None
    return dict(zip(names, values, strict=True))


async def _typesafe_score(
    client: TypeSafeClient,
    state: dict[str, Any],
    question_map: dict,
    sample_index: int,
    composite: bool,
) -> tuple[float | None, float, float, str | None]:
    answer = await client.ask(state, question_map, sample_index=sample_index)
    try:
        if composite:
            score = questions.compose_judge_score(
                {
                    name: float(answer.payload[name]["score"])
                    for name in questions.JUDGE_COMPOSITE_LEVELS
                }
            )
        else:
            score = float(answer.payload["single"]["score"])
    except (KeyError, TypeError, ValueError):
        score = None
    return score, answer.latency_s, answer.cost_usd, answer.model


def _wilson(successes: int, total: int) -> tuple[float, float]:
    if total == 0:
        return (0.0, 0.0)
    return wilson_interval(successes, total)


async def run(
    *,
    cases: Sequence[JudgeCase],
    labels: Mapping[str, str],
    client: TypeSafeClient | None,
    cache: JsonCache,
    score_fn: Callable[[Decision], Awaitable[float | None] | float | None] | None = None,
    samples: int = 5,
) -> dict[str, ArmReport]:
    """Run every judge arm over each case for the requested repeats."""
    if samples < 1:
        raise ValueError("samples must be at least 1")
    if client is not None:
        client.cache = cache
    active_arms = ARMS if client is not None else ARMS[:2]
    scores: dict[str, dict[str, list[float]]] = {
        arm: {case.case_id: [] for case in cases} for arm in active_arms
    }
    failures = {arm: 0 for arm in active_arms}
    costs = {arm: 0.0 for arm in active_arms}
    latencies: dict[str, list[float]] = {arm: [] for arm in active_arms}
    models: dict[str, set[str]] = {arm: set() for arm in active_arms}

    for case in cases:
        state = _state(case)
        decision = _decision(case)
        for sample_index in range(samples):
            score, latency, cost, model = await _cached_llm(
                cache=cache,
                state=state,
                arm="llm_current",
                sample_index=sample_index,
                request={"judge": "current"},
                call=(
                    (lambda: _current_score(decision))
                    if score_fn is None
                    else (lambda: _as_pair(score_fn(decision)))
                ),
            )
            _record(
                scores,
                failures,
                costs,
                latencies,
                models,
                "llm_current",
                case.case_id,
                score,
                latency,
                cost,
                model,
            )

            prompt = (
                build_judge_prompt(decision)
                + "\n\nReply with four numbers for clarity, completeness, alignment, "
                "and risk, in that order."
            )
            score, latency, cost, model = await _cached_llm(
                cache=cache,
                state=state,
                arm="llm_decomposed",
                sample_index=sample_index,
                request={"judge": "decomposed", "criteria": questions.JUDGE_COMPOSITE_LEVELS},
                call=lambda: _decomposed_score(prompt),
            )
            _record(
                scores,
                failures,
                costs,
                latencies,
                models,
                "llm_decomposed",
                case.case_id,
                score,
                latency,
                cost,
                model,
            )

            if client is not None:
                score, latency, cost, model = await _typesafe_score(
                    client,
                    state,
                    {"single": questions.judge_single_question()},
                    sample_index,
                    False,
                )
                _record(
                    scores,
                    failures,
                    costs,
                    latencies,
                    models,
                    "typesafe_single",
                    case.case_id,
                    score,
                    latency,
                    cost,
                    model,
                )
                score, latency, cost, model = await _typesafe_score(
                    client, state, questions.judge_composite_questions(), sample_index, True
                )
                _record(
                    scores,
                    failures,
                    costs,
                    latencies,
                    models,
                    "typesafe_composite",
                    case.case_id,
                    score,
                    latency,
                    cost,
                    model,
                )

    reports: dict[str, ArmReport] = {}
    for arm in active_arms:
        means = {
            case_id: statistics.fmean(repeats)
            for case_id, repeats in scores[arm].items()
            if repeats
        }
        tuning_scores = {
            case.case_id: means[case.case_id]
            for case in cases
            if case.split == "tuning" and case.case_id in means
        }
        edges = fit_band_edges(tuning_scores, labels)
        holdout = [
            case
            for case in cases
            if case.split == "holdout" and case.case_id in means and case.case_id in labels
        ]
        agreements = sum(
            to_band(means[case.case_id], edges) == labels[case.case_id] for case in holdout
        )
        mean_stdev, max_stdev = item_stdevs(scores[arm])
        attempts = len(cases) * samples
        reports[arm] = ArmReport(
            arm=arm,
            band_agreement=agreements / len(holdout) if holdout else 0.0,
            agreement_ci=_wilson(agreements, len(holdout)),
            mean_item_stdev=mean_stdev,
            max_item_stdev=max_stdev,
            parse_failure_rate=failures[arm] / attempts if attempts else 0.0,
            n_items=len(cases),
            n_attempts=attempts,
            cost_usd=costs[arm],
            latency_s_mean=statistics.fmean(latencies[arm]) if latencies[arm] else 0.0,
            model_reported=next(iter(models[arm])) if len(models[arm]) == 1 else None,
            sampling={
                # The router exposes neither temperature nor effort through
                # CompletionUsage, so they stay unrecorded rather than guessed.
                "temperature": None,
                "provider": _provider_of(models[arm], arm),
                "effort": None,
                # Every model seen, so a router that switched mid-run is visible
                # instead of collapsing model_reported to None with no trace.
                "models": sorted(models[arm]),
                "band_edges": edges,
                "scores": means,
            },
        )
    return reports


async def _routed(prompt: str) -> tuple[str | None, str | None]:
    """One router call, returning (text, model that answered)."""
    try:
        content, usage = await _complete_with_usage(TaskRequest(content=prompt), [])
    except Exception as exc:  # provider/budget failure - a failed attempt, never fatal
        logger.warning("judge arm call failed: %s", exc)
        return None, None
    return content, usage.model if usage is not None else None


async def _current_score(decision: Decision) -> tuple[float | None, str | None]:
    """The incumbent judge, with the model it resolved to.

    Reuses production's own prompt builder and score parser rather than
    `score_decision`, which returns a bare float and drops the model. The call
    underneath is the same one `score_decision` makes: `complete` is a wrapper
    around `complete_with_usage`.
    """
    raw, model = await _routed(build_judge_prompt(decision))
    if raw is None:
        return None, model
    return _parse_score(raw), model


async def _decomposed_score(prompt: str) -> tuple[float | None, str | None]:
    raw, model = await _routed(prompt)
    if raw is None:
        return None, model
    values = _parse_decomposed(raw)
    return (
        questions.compose_judge_score(values) if values is not None else None,
        model,
    )


async def _as_pair(value: Any) -> tuple[float | None, str | None]:
    """Accept an injected score_fn that returns a bare score or a (score, model) pair."""
    resolved = await _resolve(value)
    if isinstance(resolved, tuple):
        return resolved
    return resolved, None


def _record(
    scores: dict[str, dict[str, list[float]]],
    failures: dict[str, int],
    costs: dict[str, float],
    latencies: dict[str, list[float]],
    models: dict[str, set[str]],
    arm: str,
    case_id: str,
    score: float | None,
    latency: float,
    cost: float,
    model: str | None,
) -> None:
    costs[arm] += cost
    latencies[arm].append(latency)
    if model is not None:
        models[arm].add(model)
    if score is None:
        failures[arm] += 1
    else:
        scores[arm][case_id].append(score)
