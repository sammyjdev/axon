"""Tests for axon.doctor.checks.capture (dec-114)."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from axon.doctor import CheckStatus
from axon.doctor.checks.capture import (
    check_pending_backlog,
    check_quarantine_size,
    check_warnings_log,
)


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("AXON_DATA_ROOT", str(tmp_path))
    return tmp_path


class TestPendingBacklog:
    def test_empty_returns_ok(self, data_root: Path) -> None:
        result = check_pending_backlog(data_root=data_root)
        assert result.status is CheckStatus.OK

    def test_few_files_returns_ok(self, data_root: Path) -> None:
        pending = data_root / "pending"
        pending.mkdir()
        for i in range(3):
            (pending / f"f{i}.json").write_text("{}")
        result = check_pending_backlog(data_root=data_root)
        assert result.status is CheckStatus.OK
        assert "3" in result.detail

    def test_above_warn_threshold(self, data_root: Path) -> None:
        pending = data_root / "pending"
        pending.mkdir()
        for i in range(60):
            (pending / f"f{i}.json").write_text("{}")
        result = check_pending_backlog(data_root=data_root)
        assert result.status is CheckStatus.WARN

    def test_above_fail_threshold(self, data_root: Path) -> None:
        pending = data_root / "pending"
        pending.mkdir()
        for i in range(550):
            (pending / f"f{i}.json").write_text("{}")
        result = check_pending_backlog(data_root=data_root)
        assert result.status is CheckStatus.FAIL

    def test_old_file_triggers_warn_even_if_few(self, data_root: Path) -> None:
        pending = data_root / "pending"
        pending.mkdir()
        f = pending / "old.json"
        f.write_text("{}")
        # Backdate the file by 10 hours
        old_ts = time.time() - 10 * 3600
        os.utime(f, (old_ts, old_ts))
        result = check_pending_backlog(data_root=data_root)
        assert result.status is CheckStatus.WARN


class TestQuarantineSize:
    def test_no_dir_returns_ok(self, data_root: Path) -> None:
        result = check_quarantine_size(data_root=data_root)
        assert result.status is CheckStatus.OK

    def test_few_files_returns_ok(self, data_root: Path) -> None:
        q = data_root / "pending-quarantine"
        q.mkdir()
        (q / "x.json.123").write_text("{}")
        result = check_quarantine_size(data_root=data_root)
        assert result.status is CheckStatus.OK

    def test_many_files_returns_warn(self, data_root: Path) -> None:
        q = data_root / "pending-quarantine"
        q.mkdir()
        for i in range(6):
            (q / f"f{i}.json.{i}").write_text("{}")
        result = check_quarantine_size(data_root=data_root)
        assert result.status is CheckStatus.WARN


class TestWarningsLog:
    def test_no_log_returns_ok(self, data_root: Path) -> None:
        result = check_warnings_log(data_root=data_root)
        assert result.status is CheckStatus.OK

    def test_recent_burst_returns_warn(self, data_root: Path) -> None:
        log = data_root / "capture-warnings.jsonl"
        now = datetime.now(UTC).isoformat()
        lines = [
            json.dumps({"kind": "code_change", "commit_hash": "x", "reason": "lock", "ts": now})
            for _ in range(12)
        ]
        log.write_text("\n".join(lines))
        result = check_warnings_log(data_root=data_root)
        assert result.status is CheckStatus.WARN

    def test_old_warnings_do_not_trigger(self, data_root: Path) -> None:
        log = data_root / "capture-warnings.jsonl"
        old_ts = (datetime.now(UTC) - timedelta(days=2)).isoformat()
        lines = [
            json.dumps({"kind": "x", "commit_hash": "y", "reason": "z", "ts": old_ts})
            for _ in range(20)
        ]
        log.write_text("\n".join(lines))
        result = check_warnings_log(data_root=data_root)
        assert result.status is CheckStatus.OK
