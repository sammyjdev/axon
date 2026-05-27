"""CLI smoke tests for dec-111 (review, audit, validate-drafts) and
dec-112 (pending drain, recover).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from axon.adr.audit import record_rejection
from axon.adr.draft_pool import DraftRecord, write_draft
from axon.adr.gates import GateLayer, GateOutcome
from axon.cli.pb import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("AXON_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("AXON_ENGINE", str(tmp_path))
    return tmp_path


def _make_draft(commit_hash: str = "abc", dormant: bool = False) -> DraftRecord:
    record = DraftRecord(
        commit_hash=commit_hash,
        title="Adopt repository pattern",
        context="ctx",
        decision="dec",
        rationale="rat",
        failed_layer="density",
        failed_reason="reason",
    )
    record.dormant = dormant
    return record


class TestAdrReview:
    def test_lists_drafts(self, _isolate: Path) -> None:
        write_draft(_make_draft("aaa"), draft_dir=_isolate / "adr-draft")
        write_draft(_make_draft("bbb"), draft_dir=_isolate / "adr-draft")
        result = runner.invoke(app, ["adr", "review"])
        assert result.exit_code == 0
        assert "aaa"[:10] in result.stdout
        assert "bbb"[:10] in result.stdout

    def test_excludes_dormant_by_default(self, _isolate: Path) -> None:
        write_draft(_make_draft("active"), draft_dir=_isolate / "adr-draft")
        write_draft(
            _make_draft("dormy", dormant=True),
            draft_dir=_isolate / "adr-draft",
        )
        result = runner.invoke(app, ["adr", "review"])
        assert "active" in result.stdout
        assert "dormy" not in result.stdout

    def test_dormant_flag_includes(self, _isolate: Path) -> None:
        write_draft(
            _make_draft("dormy", dormant=True),
            draft_dir=_isolate / "adr-draft",
        )
        result = runner.invoke(app, ["adr", "review", "--dormant"])
        assert "dormy" in result.stdout

    def test_empty_pool_message(self, _isolate: Path) -> None:
        result = runner.invoke(app, ["adr", "review"])
        assert result.exit_code == 0
        assert "Nenhum draft" in result.stdout


class TestAdrAudit:
    def test_lists_rejections(self, _isolate: Path) -> None:
        outcome = GateOutcome(
            passed=False,
            failed_layer=GateLayer.DENSITY,
            reason="boilerplate",
            structural_mode=False,
            details={},
        )
        record_rejection(
            commit_hash="deadbeef",
            title="Some ADR",
            outcome=outcome,
            log_path=_isolate / "adr-rejected.jsonl",
        )
        result = runner.invoke(app, ["adr", "audit"])
        assert result.exit_code == 0
        assert "deadbeef"[:10] in result.stdout
        assert "density" in result.stdout

    def test_since_relative_days(self, _isolate: Path) -> None:
        outcome = GateOutcome(
            passed=False,
            failed_layer=GateLayer.L2,
            reason="x",
            structural_mode=False,
        )
        record_rejection(
            commit_hash="x",
            title="t",
            outcome=outcome,
            log_path=_isolate / "adr-rejected.jsonl",
        )
        result = runner.invoke(app, ["adr", "audit", "--since", "30d"])
        assert result.exit_code == 0
        # Entry was just written; should be visible
        assert "audit" in result.stdout.lower() or "x" in result.stdout

    def test_empty_log_message(self, _isolate: Path) -> None:
        result = runner.invoke(app, ["adr", "audit"])
        assert result.exit_code == 0
        assert "Nenhuma" in result.stdout


class TestPendingDrain:
    def test_drain_empty_pending(self, _isolate: Path) -> None:
        result = runner.invoke(app, ["pending", "drain"])
        assert result.exit_code == 0
        assert "processed=0" in result.stdout


class TestPendingRecover:
    def test_recover_moves_files_back(self, _isolate: Path) -> None:
        q_dir = _isolate / "pending-quarantine"
        q_dir.mkdir(parents=True, exist_ok=True)
        (q_dir / "abc.json.123").write_text(json.dumps({"kind": "code_change"}))
        result = runner.invoke(app, ["pending", "recover"])
        assert result.exit_code == 0
        assert "recovered" in result.stdout.lower()
        # File should now be in pending/
        assert any((_isolate / "pending").iterdir())

    def test_recover_no_quarantine_message(self, _isolate: Path) -> None:
        result = runner.invoke(app, ["pending", "recover"])
        assert result.exit_code == 0
        assert "quarantine" in result.stdout.lower()
