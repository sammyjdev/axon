"""Tests for axon.doctor.checks.adr (dec-114)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from axon.adr.draft_pool import DraftRecord, write_draft
from axon.doctor import CheckStatus
from axon.doctor.checks.adr import check_stale_pending


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("AXON_DATA_ROOT", str(tmp_path))
    return tmp_path


def _record(commit_hash: str) -> DraftRecord:
    return DraftRecord(
        commit_hash=commit_hash,
        title="t",
        context="c",
        decision="d",
        rationale="r",
        failed_layer="density",
        failed_reason="reason",
    )


class TestStalePending:
    def test_no_drafts_returns_ok(self, data_root: Path) -> None:
        result = check_stale_pending(data_root=data_root)
        assert result.status is CheckStatus.OK

    def test_recent_drafts_not_stale(self, data_root: Path) -> None:
        record = _record("fresh")
        record.last_l1_full_at = datetime.now(UTC)
        write_draft(record, draft_dir=data_root / "adr-draft")
        result = check_stale_pending(data_root=data_root)
        assert result.status is CheckStatus.OK

    def test_stale_drafts_warn(self, data_root: Path) -> None:
        record = _record("stale")
        record.created_at = datetime.now(UTC) - timedelta(hours=48)
        # never L1-full validated
        write_draft(record, draft_dir=data_root / "adr-draft")
        result = check_stale_pending(data_root=data_root)
        assert result.status is CheckStatus.WARN

    def test_many_stale_drafts_fail(self, data_root: Path) -> None:
        for i in range(12):
            r = _record(f"stale{i}")
            r.created_at = datetime.now(UTC) - timedelta(hours=48)
            write_draft(r, draft_dir=data_root / "adr-draft")
        result = check_stale_pending(data_root=data_root)
        assert result.status is CheckStatus.FAIL
