"""Tests for the TypeSafe pilot evaluation CLI."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from axon.benchmark.typesafe import corpus, evaluate, judge_eval
from axon.core.decision import Decision


def _decision(number: int, *, status: str = "draft") -> Decision:
    return Decision(
        id=f"dec-{number:03d}",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=number),
        agent="manual",
        repo="axon",
        files=[Path("shared.py")],
        symbols=["Shared"],
        summary=("replace previous decision" if number == 2 else "previous decision"),
        status=status,  # type: ignore[arg-type]
    )


class _Store:
    def __init__(self, decisions: list[Decision]) -> None:
        self.decisions = decisions

    async def init(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def all_decisions(self) -> list[Decision]:
        return self.decisions


def _supersession_case(*, split: str = "holdout") -> corpus.SupersessionCase:
    older, newer = _decision(1), _decision(2)
    return corpus.SupersessionCase(
        case_id="pair-1",
        older_id=older.id,
        newer_id=newer.id,
        older_summary=older.summary,
        newer_summary=newer.summary,
        older_ts=older.timestamp.isoformat(),
        newer_ts=newer.timestamp.isoformat(),
        older_status=older.status,
        shared_scope=["shared.py"],
        cosine=0.8,
        stratum="mid",
        split=split,
    )


def _write_jsonl(path: Path, items: list[dict]) -> None:
    path.write_text("".join(json.dumps(item) + "\n" for item in items))


def _install_store(monkeypatch, store: _Store) -> None:
    monkeypatch.setattr("axon.store.session_store.SessionStore", lambda: store)


def test_supersession_round_trip_writes_deterministic_conditions(tmp_path, monkeypatch) -> None:
    case = _supersession_case()
    _write_jsonl(tmp_path / "supersession_cases.jsonl", [case.__dict__])
    (tmp_path / "strata.json").write_text(
        json.dumps([{"name": "mid", "population": 3, "sampled": 1}])
    )
    _write_jsonl(tmp_path / "labels.jsonl", [{"case_id": case.case_id, "label": True}])
    _install_store(monkeypatch, _Store([_decision(1), _decision(2)]))
    monkeypatch.setattr(
        evaluate, "make_embedding_similarity", lambda embedder: lambda left, right: 0.8
    )

    out = tmp_path / "report.json"
    result = CliRunner().invoke(
        evaluate.app,
        [
            "--surface",
            "supersession",
            "--repos",
            "axon",
            "--data",
            str(tmp_path),
            "--labels",
            str(tmp_path / "labels.jsonl"),
            "--no-typesafe",
            "--out",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(out.read_text())
    assert set(report["conditions"]) == {"current", "current_recalibrated", "verb_only"}
    assert report["run_metadata"]["counts"] == {"cases": 1, "labelled": 1, "skipped": 0}


def test_judge_round_trip_uses_stubbed_score_seam(tmp_path, monkeypatch) -> None:
    case = corpus.JudgeCase("dec-001", "decision", "axon", ["shared.py"], ["Shared"], "holdout")
    _write_jsonl(tmp_path / "judge_cases.jsonl", [case.__dict__])
    _write_jsonl(tmp_path / "labels.jsonl", [{"case_id": case.case_id, "label": "ok"}])
    _install_store(monkeypatch, _Store([]))

    async def fake_run(**kwargs):
        assert kwargs["client"] is None
        assert kwargs["cache"].path == tmp_path / "cache.json"
        report = judge_eval.ArmReport(
            arm="llm_current",
            band_agreement=1.0,
            agreement_ci=(1.0, 1.0),
            mean_item_stdev=0.0,
            max_item_stdev=0.0,
            parse_failure_rate=0.0,
            infra_failure_rate=0.0,
            n_items=1,
            n_attempts=1,
            cost_usd=0.0,
            latency_s_mean=0.0,
            model_reported=None,
            sampling={},
        )
        return {"llm_current": report, "llm_decomposed": report}

    monkeypatch.setattr(evaluate.judge_eval, "run", fake_run)
    out = tmp_path / "report.json"
    result = CliRunner().invoke(
        evaluate.app,
        [
            "--surface",
            "judge",
            "--repos",
            "axon",
            "--data",
            str(tmp_path),
            "--labels",
            str(tmp_path / "labels.jsonl"),
            "--no-typesafe",
            "--out",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(out.read_text())
    assert set(report) == {"llm_current", "llm_decomposed", "run_metadata"}
    assert report["run_metadata"]["counts"] == {"cases": 1, "labelled": 1, "skipped": 0}


def test_unlabelled_case_is_skipped_not_negative(tmp_path, monkeypatch) -> None:
    labelled = _supersession_case()
    skipped = _supersession_case(split="tuning")
    skipped = corpus.SupersessionCase(**{**skipped.__dict__, "case_id": "pair-2"})
    _write_jsonl(tmp_path / "supersession_cases.jsonl", [labelled.__dict__, skipped.__dict__])
    (tmp_path / "strata.json").write_text(
        json.dumps([{"name": "mid", "population": 2, "sampled": 2}])
    )
    _write_jsonl(tmp_path / "labels.jsonl", [{"case_id": labelled.case_id, "label": True}])
    _install_store(monkeypatch, _Store([_decision(1), _decision(2)]))
    monkeypatch.setattr(
        evaluate, "make_embedding_similarity", lambda embedder: lambda left, right: 0.8
    )

    out = tmp_path / "report.json"
    result = CliRunner().invoke(
        evaluate.app,
        [
            "--surface",
            "supersession",
            "--repos",
            "axon",
            "--data",
            str(tmp_path),
            "--labels",
            str(tmp_path / "labels.jsonl"),
            "--no-typesafe",
            "--out",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(out.read_text())
    assert report["run_metadata"]["counts"] == {"cases": 2, "labelled": 1, "skipped": 1}
    assert report["conditions"]["current"]["aggregate"]["counts"]["tn"] == 0


def test_invalid_label_names_case_id(tmp_path) -> None:
    _write_jsonl(tmp_path / "labels.jsonl", [{"case_id": "pair-typo", "label": "perhaps"}])

    result = CliRunner().invoke(
        evaluate.app,
        [
            "--surface",
            "supersession",
            "--repos",
            "axon",
            "--data",
            str(tmp_path),
            "--labels",
            str(tmp_path / "labels.jsonl"),
            "--no-typesafe",
        ],
    )

    assert result.exit_code != 0
    assert "pair-typo" in result.output


def test_unhashable_label_still_names_the_case_id(tmp_path) -> None:
    """A malformed label must not lose the case id it belongs to."""
    _write_jsonl(tmp_path / "labels.jsonl", [{"case_id": "pair-listy", "label": ["ok"]}])

    result = CliRunner().invoke(
        evaluate.app,
        [
            "--surface",
            "judge",
            "--repos",
            "axon",
            "--data",
            str(tmp_path),
            "--labels",
            str(tmp_path / "labels.jsonl"),
            "--no-typesafe",
        ],
    )

    assert result.exit_code != 0
    assert "pair-listy" in result.output


def test_missing_repos_fails_before_store_or_report(tmp_path, monkeypatch) -> None:
    class UnreadableStore:
        def __init__(self) -> None:
            raise AssertionError("store must not be opened")

    monkeypatch.setattr("axon.store.session_store.SessionStore", UnreadableStore)
    out = tmp_path / "report.json"
    result = CliRunner().invoke(
        evaluate.app,
        ["--surface", "judge", "--data", str(tmp_path), "--no-typesafe", "--out", str(out)],
    )

    assert result.exit_code != 0
    assert not out.exists()


async def test_load_decisions_names_missing_ids() -> None:
    with pytest.raises(ValueError, match="dec-002"):
        await evaluate.load_decisions(
            store=_Store([_decision(1)]),
            allowed_repos=frozenset({"axon"}),
            case_ids={"dec-001", "dec-002"},
        )


def test_the_live_store_status_never_reaches_the_current_arm(tmp_path, monkeypatch) -> None:
    """Replaces test_stored_superseded_status_reaches_current_arm.

    That test pinned the opposite contract: the arm read ``Decision.status``
    from the store at run time, so production marking a decision superseded
    changed the measured number after the corpus was frozen. Review finding 1
    (2026-09-22) called it irreproducible and circular, since the status is
    written by the detector under test. The corpus now pins the status; the
    store is seeded with the opposite value here to prove it is ignored.
    """
    case = _supersession_case()
    _write_jsonl(tmp_path / "supersession_cases.jsonl", [case.__dict__])
    (tmp_path / "strata.json").write_text(
        json.dumps([{"name": "mid", "population": 1, "sampled": 1}])
    )
    _write_jsonl(tmp_path / "labels.jsonl", [{"case_id": case.case_id, "label": True}])
    _install_store(monkeypatch, _Store([_decision(1, status="superseded"), _decision(2)]))
    monkeypatch.setattr(
        evaluate, "make_embedding_similarity", lambda embedder: lambda left, right: 0.0
    )

    out = tmp_path / "report.json"
    result = CliRunner().invoke(
        evaluate.app,
        [
            "--surface",
            "supersession",
            "--repos",
            "axon",
            "--data",
            str(tmp_path),
            "--labels",
            str(tmp_path / "labels.jsonl"),
            "--no-typesafe",
            "--out",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(out.read_text())["conditions"]["current"]["aggregate"]["counts"]["tp"] == 0


def test_missing_typesafe_key_exits_cleanly(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    result = CliRunner().invoke(
        evaluate.app,
        ["--surface", "judge", "--repos", "axon", "--data", str(tmp_path)],
    )

    assert result.exit_code != 0
    assert "TYPESAFE_API_KEY" in result.output
    assert "Traceback" not in result.output
