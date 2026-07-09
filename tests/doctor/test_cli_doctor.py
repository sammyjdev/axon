"""CLI smoke tests for pb doctor 3 modes (dec-114)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from axon.cli.pb import app

runner = CliRunner(mix_stderr=False)


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("AXON_ENGINE", str(tmp_path))
    monkeypatch.setenv("AXON_DATA_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestDefaultMode:
    def test_runs_and_shows_checks_section(self, _isolate: Path) -> None:
        result = runner.invoke(app, ["doctor"])
        # Exit code reflects severity. Empty env may return 0/1/2 — accept any
        # since the existing platform/runtime checks may emit notes.
        assert result.exit_code in (0, 1, 2)
        assert "capture & adr checks" in result.stdout


class TestCIMode:
    def test_ci_emits_valid_json_and_exits_zero(self, _isolate: Path) -> None:
        result = runner.invoke(app, ["doctor", "--ci"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["version"] == "1"
        assert isinstance(payload["checks"], list)
        assert {"ok", "warn", "fail"} <= set(payload["summary"].keys())

    def test_ci_with_problems_still_exits_zero(self, _isolate: Path) -> None:
        # Create a problem (many stale drafts) to force a FAIL
        from datetime import UTC, datetime, timedelta

        from axon.adr.draft_pool import DraftRecord, write_draft

        for i in range(12):
            record = DraftRecord(
                commit_hash=f"stale{i}",
                title="t",
                context="c",
                decision="d",
                rationale="r",
                failed_layer="density",
                failed_reason="r",
            )
            record.created_at = datetime.now(UTC) - timedelta(hours=48)
            write_draft(record, draft_dir=_isolate / "adr-draft")

        result = runner.invoke(app, ["doctor", "--ci"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["summary"]["fail"] >= 1

    def test_ci_reports_index_composition_skipped_when_db_unreachable(
        self, _isolate: Path
    ) -> None:
        with patch(
            "axon.doctor.checks.index_composition.asyncpg.connect",
            side_effect=OSError("connection refused"),
        ):
            result = runner.invoke(app, ["doctor", "--ci"])

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        check = next(c for c in payload["checks"] if c["name"] == "index.composition")
        assert check["status"] == "warn"
        assert check["detail"] == "skipped: db unreachable"

    def test_ci_includes_continuous_accounting_checks(self, _isolate: Path) -> None:
        result = runner.invoke(app, ["doctor", "--ci"])

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        names = {c["name"] for c in payload["checks"]}
        assert "recall.savings" in names
        assert "install.branch" in names


class TestApplyMode:
    def test_apply_without_tty_refuses(self, _isolate: Path) -> None:
        # Force no-TTY deterministically (running from a real terminal would
        # otherwise leave os.isatty(0) True and the refusal path unexercised).
        with patch("os.isatty", return_value=False):
            result = runner.invoke(app, ["doctor", "--apply"])
        assert result.exit_code == 1
        assert "TTY" in result.stderr

    def test_apply_and_ci_mutually_exclusive(self, _isolate: Path) -> None:
        with patch("os.isatty", return_value=True):
            result = runner.invoke(app, ["doctor", "--apply", "--ci"])
        assert result.exit_code == 2
        # Error goes to stderr (Click >= 8.2 no longer mixes streams).
        assert "mutuamente exclusivos" in result.stderr or "mutually" in result.stderr
