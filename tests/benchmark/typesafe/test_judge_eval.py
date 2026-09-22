"""Tests for judge benchmark measurement."""

from __future__ import annotations

import json

import httpx

from axon.benchmark.typesafe import judge_eval, questions
from axon.benchmark.typesafe.client import Answer, JsonCache, TypeSafeClient
from axon.benchmark.typesafe.corpus import JudgeCase
from axon.router.engine import CompletionUsage


def _cases() -> list[JudgeCase]:
    return [
        JudgeCase("dec-001", "weak", "axon", ["a.py"], ["A"], "tuning"),
        JudgeCase("dec-002", "ok", "axon", ["b.py"], ["B"], "tuning"),
        JudgeCase("dec-003", "strong", "axon", ["c.py"], ["C"], "holdout"),
    ]


class _Client:
    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload or {
            "single": {"score": 3.0},
            **{name: {"score": 1.0} for name in questions.JUDGE_COMPOSITE_LEVELS},
        }
        self.calls: list[tuple[dict, dict, int]] = []

    async def ask(self, state: dict, question_map: dict, *, sample_index: int) -> Answer:
        self.calls.append((state, question_map, sample_index))
        return Answer(self.payload, "jev", 10, 0, 0.2, 0.01, False)


async def test_typesafe_state_contains_only_incumbent_prompt_fields(tmp_path) -> None:
    client = _Client()
    await judge_eval.run(
        cases=_cases(),
        labels={"dec-001": "weak", "dec-002": "ok", "dec-003": "strong"},
        client=client,
        cache=JsonCache(tmp_path / "cache.json"),
        score_fn=lambda decision: 3.0,
        samples=1,
    )

    assert set(client.calls[0][0]) == {"id", "summary", "repo", "files", "symbols"}
    assert client.calls[0][0] == {
        "id": "dec-001",
        "summary": "weak",
        "repo": "axon",
        "files": ["a.py"],
        "symbols": ["A"],
    }


async def test_none_current_score_is_parse_failure_not_zero(tmp_path) -> None:
    async def no_score(decision) -> None:
        return None

    report = await judge_eval.run(
        cases=_cases(),
        labels={"dec-001": "weak", "dec-002": "ok", "dec-003": "strong"},
        client=None,
        cache=JsonCache(tmp_path / "cache.json"),
        score_fn=no_score,
        samples=2,
    )

    current = report["llm_current"]
    assert current.parse_failure_rate == 1.0
    assert current.mean_item_stdev == 0.0
    assert current.band_agreement == 0.0


async def test_all_failed_item_is_excluded_from_stdev(tmp_path) -> None:
    # The surviving items must have DIFFERENT spreads, otherwise "excluded" and
    # "included with a fabricated 0.0" produce the same mean and the test cannot
    # tell them apart. dec-001 varies (pstdev 1.0), dec-002 is flat (0.0):
    # excluding dec-003 gives 0.5, counting it as stable would give 0.333.
    seen: dict[str, int] = {}

    async def sometimes(decision) -> float | None:
        if decision.id == "dec-003":
            return None
        attempt = seen.get(decision.id, 0)
        seen[decision.id] = attempt + 1
        if decision.id == "dec-001":
            return 1.0 if attempt == 0 else 3.0
        return 4.0

    report = await judge_eval.run(
        cases=_cases(),
        labels={"dec-001": "weak", "dec-002": "ok", "dec-003": "strong"},
        client=None,
        cache=JsonCache(tmp_path / "cache.json"),
        score_fn=sometimes,
        samples=2,
    )

    current = report["llm_current"]
    assert current.mean_item_stdev == 0.5
    assert current.max_item_stdev == 1.0
    assert current.parse_failure_rate == 2 / 6


def test_item_stdevs_skips_an_item_with_no_surviving_repeats() -> None:
    assert judge_eval.item_stdevs({"varies": [1.0, 3.0], "failed": []}) == (1.0, 1.0)


def test_population_stdev_is_meaned_per_item() -> None:
    mean, maximum = judge_eval.item_stdevs({"a": [1.0, 3.0], "b": [2.0, 2.0]})

    assert mean == 0.5
    assert maximum == 1.0
    assert judge_eval.item_stdevs({"stable": [2.0, 2.0]}) == (0.0, 0.0)


def test_fit_band_edges_is_deterministic_and_ties_default() -> None:
    scores = {"a": 1.0, "b": 3.0, "c": 5.0}
    labels = {"a": "weak", "b": "ok", "c": "strong"}

    assert judge_eval.fit_band_edges(scores, labels) == judge_eval.fit_band_edges(scores, labels)
    assert judge_eval.fit_band_edges(scores, labels) == (2.5, 3.5)
    assert judge_eval.fit_band_edges({"a": 3.0}, {"a": "ok"}) == judge_eval.DEFAULT_BAND_EDGES


async def test_holdout_labels_do_not_move_fitted_edges(tmp_path) -> None:
    cases = _cases()

    async def scores(decision) -> float:
        return {"dec-001": 1.0, "dec-002": 3.0, "dec-003": 5.0}[decision.id]

    common = dict(
        cases=cases,
        client=None,
        cache=JsonCache(tmp_path / "cache.json"),
        score_fn=scores,
        samples=1,
    )
    first = await judge_eval.run(
        labels={"dec-001": "weak", "dec-002": "ok", "dec-003": "strong"}, **common
    )
    second = await judge_eval.run(
        labels={"dec-001": "weak", "dec-002": "ok", "dec-003": "weak"}, **common
    )

    assert (
        first["llm_current"].sampling["band_edges"] == second["llm_current"].sampling["band_edges"]
    )


async def test_per_arm_edges_make_uniformly_shifted_scores_equally_accurate(tmp_path) -> None:
    cases = _cases()

    async def original(decision) -> float:
        return {"dec-001": 1.0, "dec-002": 3.0, "dec-003": 5.0}[decision.id]

    async def shifted(decision) -> float:
        return await original(decision) + 1.0

    labels = {"dec-001": "weak", "dec-002": "ok", "dec-003": "strong"}
    first = await judge_eval.run(
        cases=cases,
        labels=labels,
        client=None,
        cache=JsonCache(tmp_path / "one.json"),
        score_fn=original,
        samples=1,
    )
    second = await judge_eval.run(
        cases=cases,
        labels=labels,
        client=None,
        cache=JsonCache(tmp_path / "two.json"),
        score_fn=shifted,
        samples=1,
    )

    assert first["llm_current"].band_agreement == second["llm_current"].band_agreement


async def test_llm_arms_use_shared_cache_on_second_run(tmp_path, monkeypatch) -> None:
    calls = 0

    async def score(decision) -> float:
        nonlocal calls
        calls += 1
        return 3.0

    async def complete(task, messages):
        nonlocal calls
        calls += 1
        return "1 1 1 1", CompletionUsage(
            model="openrouter/anthropic/claude-haiku-4",
            prompt_tokens=10,
            completion_tokens=2,
            total_tokens=12,
        )

    monkeypatch.setattr(judge_eval, "_complete_with_usage", complete)
    args = dict(
        cases=_cases(),
        labels={"dec-001": "weak", "dec-002": "ok", "dec-003": "strong"},
        client=None,
        cache=JsonCache(tmp_path / "cache.json"),
        score_fn=score,
        samples=1,
    )
    await judge_eval.run(**args)
    first_calls = calls
    await judge_eval.run(**args)

    assert first_calls == 6
    assert calls == first_calls


async def test_composite_arms_use_same_composition(tmp_path, monkeypatch) -> None:
    values = {name: 1.0 for name in questions.JUDGE_COMPOSITE_LEVELS}
    client = _Client(
        {"single": {"score": 3.0}, **{name: {"score": value} for name, value in values.items()}}
    )

    async def complete(task, messages):
        return "1 1 1 1", CompletionUsage(
            model="openrouter/anthropic/claude-haiku-4",
            prompt_tokens=10,
            completion_tokens=2,
            total_tokens=12,
        )

    monkeypatch.setattr(judge_eval, "_complete_with_usage", complete)
    report = await judge_eval.run(
        cases=_cases(),
        labels={"dec-001": "weak", "dec-002": "ok", "dec-003": "strong"},
        client=client,
        cache=JsonCache(tmp_path / "cache.json"),
        score_fn=lambda decision: 3.0,
        samples=1,
    )

    assert (
        report["llm_decomposed"].sampling["scores"]["dec-001"]
        == report["typesafe_composite"].sampling["scores"]["dec-001"]
    )


async def test_every_arm_including_typesafe_is_free_on_a_second_run(tmp_path, monkeypatch) -> None:
    """A repeat of the whole measurement must cost nothing, in all four arms.

    The stub client above never touches the cache, so it cannot prove this. Here
    the real TypeSafeClient runs over a MockTransport that counts HTTP calls.
    """
    http_calls = 0
    llm_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal http_calls
        http_calls += 1
        asked = json.loads(request.content)["questions"]
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {name: {"type": "score", "score": 1.0} for name in asked},
                "usage": {"input_tokens": 100, "output_tokens": 0},
            },
        )

    async def complete(task, messages):
        nonlocal llm_calls
        llm_calls += 1
        return "1 1 1 1", CompletionUsage(
            model="openrouter/anthropic/claude-haiku-4",
            prompt_tokens=10,
            completion_tokens=2,
            total_tokens=12,
        )

    async def score(decision) -> float:
        nonlocal llm_calls
        llm_calls += 1
        return 3.0

    monkeypatch.setattr(judge_eval, "_complete_with_usage", complete)
    cache = JsonCache(tmp_path / "cache.json")
    args = dict(
        cases=_cases(),
        labels={"dec-001": "weak", "dec-002": "ok", "dec-003": "strong"},
        client=TypeSafeClient(
            cache=cache, api_key="test", transport=httpx.MockTransport(handler)
        ),
        cache=cache,
        score_fn=score,
        samples=1,
    )

    await judge_eval.run(**args)
    first_http, first_llm = http_calls, llm_calls
    assert first_http > 0 and first_llm > 0

    await judge_eval.run(**args)

    assert (http_calls, llm_calls) == (first_http, first_llm)


async def test_llm_arms_record_the_model_the_router_resolved(tmp_path, monkeypatch) -> None:
    """complete() drops usage, so an arm built on it can never say who answered."""

    async def complete(task, messages):
        return "1 1 1 1", CompletionUsage(
            model="openrouter/anthropic/claude-haiku-4",
            prompt_tokens=10,
            completion_tokens=2,
            total_tokens=12,
        )

    monkeypatch.setattr(judge_eval, "_complete_with_usage", complete)
    report = await judge_eval.run(
        cases=_cases(),
        labels={"dec-001": "weak", "dec-002": "ok", "dec-003": "strong"},
        client=None,
        cache=JsonCache(tmp_path / "cache.json"),
        samples=1,
    )

    for arm in ("llm_current", "llm_decomposed"):
        assert report[arm].model_reported == "openrouter/anthropic/claude-haiku-4"
        assert report[arm].sampling["provider"] == "openrouter"
        assert report[arm].sampling["models"] == ["openrouter/anthropic/claude-haiku-4"]


async def test_no_client_reports_only_llm_arms(tmp_path) -> None:
    report = await judge_eval.run(
        cases=_cases(),
        labels={"dec-001": "weak", "dec-002": "ok", "dec-003": "strong"},
        client=None,
        cache=JsonCache(tmp_path / "cache.json"),
        score_fn=lambda decision: 3.0,
        samples=1,
    )

    assert set(report) == {"llm_current", "llm_decomposed"}
