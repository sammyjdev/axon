"""Tests for the TypeSafe pilot corpus extraction."""

from __future__ import annotations

import json
import random
from collections import Counter
from datetime import UTC, datetime, timedelta
from itertools import combinations
from pathlib import Path

import pytest
from typer.testing import CliRunner

from axon.benchmark.supersession import _FakeStore
from axon.benchmark.typesafe import corpus, questions
from axon.core.decision import Decision, Status


class _CorpusStore(_FakeStore):
    async def all_decisions(self) -> list[Decision]:
        return list(self._decisions)


def _decision(
    number: int,
    *,
    repo: str = "axon",
    file: str = "shared.py",
    symbol: str | None = None,
    summary: str | None = None,
    judged: bool = False,
    validation_score: float = 0.0,
    status: Status = "draft",
) -> Decision:
    return Decision(
        id=f"dec-{number:03d}",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=number),
        agent="manual",
        repo=repo,
        files=[Path(file)] if file else [],
        symbols=[symbol] if symbol else [],
        summary=summary or f"decision {number}",
        judged=judged,
        validation_score=validation_score,
        status=status,
    )


async def test_repo_allowlist_is_fail_closed_for_both_corpora() -> None:
    decisions = (
        _decision(1),
        _decision(2),
        _decision(3, repo="private"),
        _decision(4, repo="private"),
    )
    store = _CorpusStore(decisions)

    supersession, _, _dropped = await corpus.build_supersession_corpus(
        store=store,
        allowed_repos=frozenset({"axon"}),
        similarity=lambda left, right: 1.0,
        min_low_stratum=0,
    )
    judge, _ = await corpus.build_judge_corpus(
        store=store,
        allowed_repos=frozenset({"axon"}),
    )

    assert {case.older_id for case in supersession} | {case.newer_id for case in supersession} == {
        "dec-001",
        "dec-002",
    }
    assert {case.repo for case in judge} == {"axon"}


@pytest.mark.parametrize("builder", [corpus.build_supersession_corpus, corpus.build_judge_corpus])
async def test_empty_repo_allowlist_is_rejected(builder) -> None:
    with pytest.raises(ValueError, match="allowlist is mandatory"):
        await builder(store=_CorpusStore(()), allowed_repos=frozenset())


@pytest.mark.parametrize("repos", [[], ["--repos", ""]])
def test_cli_rejects_missing_or_empty_allowlist_without_writing(tmp_path, repos: list[str]) -> None:
    out = tmp_path / "corpus"

    result = CliRunner().invoke(
        corpus.app,
        ["--surface", "judge", *repos, "--out", str(out)],
    )

    assert result.exit_code != 0
    assert "allowlist is mandatory" in result.output.lower()
    assert "restricted" in result.output.lower()
    assert not out.exists()


def test_cli_writes_separate_unlabeled_artifacts_and_summary(tmp_path, monkeypatch) -> None:
    class FakeSessionStore:
        async def init(self) -> None:
            pass

        async def close(self) -> None:
            pass

    async def fake_supersession(**kwargs):
        return (
            [
                corpus.SupersessionCase(
                    case_id="pair",
                    older_id="dec-001",
                    newer_id="dec-002",
                    older_summary="old",
                    newer_summary="new",
                    older_ts="2026-01-01T00:00:00+00:00",
                    newer_ts="2026-01-02T00:00:00+00:00",
                    older_status="active",
                    shared_scope=["shared.py"],
                    cosine=0.5,
                    stratum="low",
                    split="holdout",
                )
            ],
            [corpus.Stratum("low", population=8, sampled=1)],
            (),
        )

    async def fake_judge(**kwargs):
        return [
            corpus.JudgeCase(
                case_id="dec-001",
                summary="decision",
                repo="axon",
                files=["shared.py"],
                symbols=[],
                split="tuning",
            )
        ], [{"case_id": "dec-001", "validation_score": 4.0, "judged": True}]

    monkeypatch.setattr("axon.store.session_store.SessionStore", FakeSessionStore)
    monkeypatch.setattr(corpus, "build_supersession_corpus", fake_supersession)
    monkeypatch.setattr(corpus, "build_judge_corpus", fake_judge)
    out = tmp_path / "corpus"
    runner = CliRunner()

    supersession = runner.invoke(
        corpus.app,
        ["--surface", "supersession", "--repos", "axon", "--out", str(out)],
    )
    judge = runner.invoke(
        corpus.app,
        ["--surface", "judge", "--repos", "axon", "--out", str(out)],
    )

    assert supersession.exit_code == judge.exit_code == 0
    assert {path.name for path in out.iterdir()} == {
        "supersession_cases.jsonl",
        "strata.json",
        "sampling.json",
        "judge_cases.jsonl",
        "judge_history.jsonl",
    }
    supersession_case = json.loads((out / "supersession_cases.jsonl").read_text())
    judge_case = json.loads((out / "judge_cases.jsonl").read_text())
    judge_history = json.loads((out / "judge_history.jsonl").read_text())
    assert "label" not in supersession_case
    assert "label" not in judge_case
    assert "validation_score" not in judge_case and "judged" not in judge_case
    assert judge_history == {"case_id": "dec-001", "validation_score": 4.0, "judged": True}
    assert "low: population=8 sampled=1" in supersession.output
    assert "splits: tuning=0 holdout=1" in supersession.output
    assert f"embedder: {questions.EMBEDDER_MODEL}" in supersession.output
    assert "splits: tuning=1 holdout=0" in judge.output
    assert "embedder: none" in judge.output


async def test_supersession_requires_scope_intersection() -> None:
    store = _CorpusStore(
        (
            _decision(1, file="first.py", symbol="first"),
            _decision(2, file="second.py", symbol="second"),
        )
    )

    cases, strata, _dropped = await corpus.build_supersession_corpus(
        store=store,
        allowed_repos=frozenset({"axon"}),
        similarity=lambda left, right: 1.0,
        min_low_stratum=0,
    )

    assert cases == []
    assert sum(stratum.population for stratum in strata) == 0


async def test_strata_use_pinned_production_boundaries() -> None:
    decisions = (
        _decision(1, repo="low", summary="low older"),
        _decision(2, repo="low", summary="low newer"),
        _decision(3, repo="mid", summary="mid older"),
        _decision(4, repo="mid", summary="mid newer"),
        _decision(5, repo="high", summary="high older"),
        _decision(6, repo="high", summary="high newer"),
        # Exactly on the low boundary: production compares with a strict "<", so
        # equality belongs to "mid". Pinned on both edges so a later ">=" flip fails.
        _decision(7, repo="edge", summary="edge older"),
        _decision(8, repo="edge", summary="edge newer"),
    )
    scores = {
        frozenset({"low older", "low newer"}): questions.SCOPE_SIM_THRESHOLD - 0.001,
        frozenset({"edge older", "edge newer"}): questions.SCOPE_SIM_THRESHOLD,
        frozenset({"mid older", "mid newer"}): (
            questions.SCOPE_SIM_THRESHOLD + questions.NEAR_DUP_THRESHOLD
        )
        / 2,
        frozenset({"high older", "high newer"}): questions.NEAR_DUP_THRESHOLD,
    }

    cases, _, _dropped = await corpus.build_supersession_corpus(
        store=_CorpusStore(decisions),
        allowed_repos=frozenset({"low", "mid", "high", "edge"}),
        similarity=lambda left, right: scores[frozenset({left, right})],
        target=4,
        min_low_stratum=1,
    )

    assert {case.older_summary.split()[0]: case.stratum for case in cases} == {
        "low": "low",
        "mid": "mid",
        "high": "high",
        "edge": "mid",
    }


async def test_stratum_population_is_counted_before_sampling() -> None:
    decisions = tuple(_decision(number) for number in range(1, 7))

    cases, strata, _dropped = await corpus.build_supersession_corpus(
        store=_CorpusStore(decisions),
        allowed_repos=frozenset({"axon"}),
        similarity=lambda left, right: 1.0,
        target=3,
        min_low_stratum=0,
    )

    high = next(stratum for stratum in strata if stratum.name == "high")
    assert len(cases) == 3
    assert high.population == 15
    assert high.population > high.sampled


async def test_low_stratum_shortfall_reports_available_count() -> None:
    decisions = tuple(_decision(number) for number in range(1, 4))

    with pytest.raises(ValueError, match=r"low stratum.*3.*available"):
        await corpus.build_supersession_corpus(
            store=_CorpusStore(decisions),
            allowed_repos=frozenset({"axon"}),
            similarity=lambda left, right: 0.1,
            target=10,
            min_low_stratum=4,
        )


async def test_sample_keeps_the_low_floor_and_every_mid_pair() -> None:
    """Quota is 40 low, a mid census, and the rest high. Proportional fill is not it."""
    decisions = tuple(_decision(number, summary=f"s{number}") for number in range(23))
    pairs = list(combinations((decision.summary for decision in decisions), 2))
    scores = {}
    for index, pair in enumerate(pairs):
        if index < 100:
            scores[frozenset(pair)] = 0.1
        elif index < 157:
            scores[frozenset(pair)] = 0.85
        else:
            scores[frozenset(pair)] = 0.95

    cases, strata, _dropped = await corpus.build_supersession_corpus(
        store=_CorpusStore(decisions),
        allowed_repos=frozenset({"axon"}),
        similarity=lambda left, right: scores[frozenset({left, right})],
        target=150,
        min_low_stratum=40,
        seed=questions.SAMPLE_SEED,
    )

    counts = Counter(case.stratum for case in cases)
    assert counts == Counter(low=40, mid=57, high=53)
    mid = next(stratum for stratum in strata if stratum.name == "mid")
    assert mid.population == 57
    assert mid.sampled == 57
    assert len({(case.older_summary, case.newer_summary) for case in cases}) == len(cases)


def test_duplicate_summary_pairs_are_refilled_from_the_same_stratum() -> None:
    def pair(case_id: str, older: str, newer: str) -> corpus.SupersessionCase:
        return corpus.SupersessionCase(
            case_id=case_id,
            older_id=f"{case_id}-o",
            newer_id=f"{case_id}-n",
            older_summary=older,
            newer_summary=newer,
            older_ts="2026-01-01T00:00:00+00:00",
            newer_ts="2026-01-02T00:00:00+00:00",
            older_status="active",
            shared_scope=["shared.py"],
            cosine=0.99,
            stratum="high",
            split="",
        )

    duplicate = pair("dup-a", "same old", "same new")
    copy = pair("dup-b", "same old", "same new")
    refill = pair("unique", "other old", "other new")
    population = {"low": [], "mid": [], "high": [duplicate, copy, refill]}

    kept, dropped = corpus.collapse_duplicate_summaries(
        [duplicate, copy],
        population,
        random.Random(questions.SAMPLE_SEED),  # noqa: S311 - reproducible sampling, not security
    )

    assert dropped == ("dup-b",)
    assert [case.case_id for case in kept] == ["dup-a", "unique"]


def test_refill_keeps_one_case_when_the_pool_repeats_a_summary() -> None:
    def pair(case_id: str, older: str, newer: str) -> corpus.SupersessionCase:
        return corpus.SupersessionCase(
            case_id=case_id,
            older_id=f"{case_id}-o",
            newer_id=f"{case_id}-n",
            older_summary=older,
            newer_summary=newer,
            older_ts="2026-01-01T00:00:00+00:00",
            newer_ts="2026-01-02T00:00:00+00:00",
            older_status="active",
            shared_scope=["shared.py"],
            cosine=0.99,
            stratum="high",
            split="",
        )

    sampled = [
        pair("a1", "old-a", "new-a"),
        pair("a2", "old-a", "new-a"),
        pair("c1", "old-c", "new-c"),
        pair("c2", "old-c", "new-c"),
    ]
    pool = [
        *sampled,
        pair("b1", "old-b", "new-b"),
        pair("b2", "old-b", "new-b"),
    ]
    kept, _dropped = corpus.collapse_duplicate_summaries(
        sampled,
        {"low": [], "mid": [], "high": pool},
        random.Random(questions.SAMPLE_SEED),  # noqa: S311 - reproducible sampling, not security
    )

    keys = [(case.older_summary, case.newer_summary) for case in kept]
    assert len(keys) == len(set(keys))
    assert len(kept) == 3


def test_pilot_sample_aborts_when_mid_tuning_is_below_the_floor() -> None:
    def mid(case_id: str, split: str) -> corpus.SupersessionCase:
        return corpus.SupersessionCase(
            case_id=case_id,
            older_id=f"{case_id}-o",
            newer_id=f"{case_id}-n",
            older_summary=f"old {case_id}",
            newer_summary=f"new {case_id}",
            older_ts="2026-01-01T00:00:00+00:00",
            newer_ts="2026-01-02T00:00:00+00:00",
            older_status="active",
            shared_scope=["shared.py"],
            cosine=0.85,
            stratum="mid",
            split=split,
        )

    short = [mid(f"m{index}", "holdout") for index in range(12)]
    with pytest.raises(ValueError, match="mid tuning"):
        corpus.require_pilot_sample(short)

    enough = [mid(f"m{index}", "tuning" if index < 10 else "holdout") for index in range(12)]
    corpus.require_pilot_sample(enough)
    corpus.require_pilot_sample([mid("only", "holdout")])


async def test_supersession_sampling_is_seeded_and_deterministic() -> None:
    decisions = tuple(_decision(number) for number in range(1, 13))

    async def sample(seed: int) -> list[str]:
        cases, _, _dropped = await corpus.build_supersession_corpus(
            store=_CorpusStore(decisions),
            allowed_repos=frozenset({"axon"}),
            similarity=lambda left, right: 1.0,
            target=8,
            min_low_stratum=0,
            seed=seed,
        )
        return [case.case_id for case in cases]

    assert await sample(11) == await sample(11)
    assert await sample(11) != await sample(12)


async def test_split_is_disjoint_complete_and_thirty_seventy() -> None:
    decisions = tuple(_decision(number) for number in range(1, 11))

    cases, _ = await corpus.build_judge_corpus(
        store=_CorpusStore(decisions),
        allowed_repos=frozenset({"axon"}),
        target=10,
        seed=7,
    )

    tuning = {case.case_id for case in cases if case.split == "tuning"}
    holdout = {case.case_id for case in cases if case.split == "holdout"}
    assert not tuning & holdout
    assert tuning | holdout == {case.case_id for case in cases}
    assert abs(len(tuning) - len(cases) * 0.3) <= 1
    assert abs(len(holdout) - len(cases) * 0.7) <= 1


async def test_judge_cases_hide_incumbent_history_and_include_unjudged() -> None:
    decisions = (
        _decision(1, judged=False, validation_score=0.0),
        _decision(2, judged=True, validation_score=4.5),
        _decision(3, status="active", judged=True, validation_score=5.0),
    )

    cases, history = await corpus.build_judge_corpus(
        store=_CorpusStore(decisions),
        allowed_repos=frozenset({"axon"}),
    )

    assert {case.case_id for case in cases} == {"dec-001", "dec-002"}
    assert all(not hasattr(case, "validation_score") for case in cases)
    assert all(not hasattr(case, "judged") for case in cases)
    assert {item["case_id"] for item in history} == {"dec-001", "dec-002"}
    assert all(set(item) == {"case_id", "validation_score", "judged"} for item in history)


async def test_embedding_retries_a_timeout_and_reuses_the_disk_cache(tmp_path, monkeypatch) -> None:
    calls: list[str] = []

    class FlakyEmbedder:
        def __init__(self, *, model_name: str) -> None:
            assert model_name == questions.EMBEDDER_MODEL

        def embed_one(self, text: str) -> list[float]:
            calls.append(text)
            if len(calls) == 1:
                raise RuntimeError("timeout")
            return [1.0, float(len(text))]

    monkeypatch.setattr(corpus, "EmbedderEngine", FlakyEmbedder)
    monkeypatch.setattr(corpus.time, "sleep", lambda _seconds: None)
    decisions = tuple(_decision(number) for number in range(1, 3))
    cache = tmp_path / "embeddings.jsonl"

    await corpus.build_supersession_corpus(
        store=_CorpusStore(decisions),
        allowed_repos=frozenset({"axon"}),
        min_low_stratum=0,
        cache_path=cache,
    )
    first_run = len(calls)
    await corpus.build_supersession_corpus(
        store=_CorpusStore(decisions),
        allowed_repos=frozenset({"axon"}),
        min_low_stratum=0,
        cache_path=cache,
    )

    assert first_run == 3
    assert len(calls) == 3


async def test_default_similarity_embeds_each_decision_once(monkeypatch) -> None:
    calls: list[str] = []

    class CountingEmbedder:
        def __init__(self, *, model_name: str) -> None:
            assert model_name == questions.EMBEDDER_MODEL

        def embed_one(self, text: str) -> list[float]:
            calls.append(text)
            return [1.0, float(len(text))]

    monkeypatch.setattr(corpus, "EmbedderEngine", CountingEmbedder)
    decisions = tuple(_decision(number) for number in range(1, 5))

    await corpus.build_supersession_corpus(
        store=_CorpusStore(decisions),
        allowed_repos=frozenset({"axon"}),
        min_low_stratum=0,
    )

    assert sorted(calls) == sorted(decision.summary for decision in decisions)
