"""Tests for axon.adr.audit (dec-111)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from axon.adr.audit import read_audit, record_rejection, record_weak_pass
from axon.adr.gates import GateLayer, GateOutcome


class TestRecordRejection:
    def test_appends_jsonl_entry(self, tmp_path: Path) -> None:
        log = tmp_path / "adr-rejected.jsonl"
        outcome = GateOutcome(
            passed=False,
            failed_layer=GateLayer.DENSITY,
            reason="no_architectural_lexicon_outside_diff",
            structural_mode=False,
            details={"ratio": 0.85},
        )
        record_rejection(
            commit_hash="deadbeef",
            title="Some ADR",
            outcome=outcome,
            log_path=log,
        )
        lines = log.read_text().strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["kind"] == "rejection"
        assert entry["commit_hash"] == "deadbeef"
        assert entry["layer"] == "density"
        assert entry["reason"].startswith("no_architectural_lexicon")
        assert entry["details"]["ratio"] == 0.85


class TestRecordWeakPass:
    def test_weak_pass_kind(self, tmp_path: Path) -> None:
        log = tmp_path / "log.jsonl"
        outcome = GateOutcome(
            passed=True,
            structural_mode=True,
            details={"ratio": 0.65},
        )
        record_weak_pass(
            commit_hash="abc",
            title="t",
            outcome=outcome,
            log_path=log,
        )
        entries = read_audit(log_path=log)
        assert len(entries) == 1
        assert entries[0]["kind"] == "weak_pass"
        assert entries[0]["structural_mode"] is True


class TestReadAudit:
    def test_filters_by_since(self, tmp_path: Path) -> None:
        log = tmp_path / "a.jsonl"
        # Write two entries manually with different timestamps
        old_ts = (datetime.now(UTC) - timedelta(days=10)).isoformat()
        new_ts = datetime.now(UTC).isoformat()
        log.write_text(
            json.dumps({"kind": "rejection", "ts": old_ts}) + "\n"
            + json.dumps({"kind": "rejection", "ts": new_ts}) + "\n"
        )
        cutoff = datetime.now(UTC) - timedelta(days=1)
        entries = read_audit(log_path=log, since=cutoff)
        assert len(entries) == 1
        assert entries[0]["ts"] == new_ts

    def test_filters_by_kind(self, tmp_path: Path) -> None:
        log = tmp_path / "k.jsonl"
        log.write_text(
            json.dumps({"kind": "rejection", "ts": datetime.now(UTC).isoformat()}) + "\n"
            + json.dumps({"kind": "weak_pass", "ts": datetime.now(UTC).isoformat()}) + "\n"
        )
        rejections = read_audit(log_path=log, kinds=("rejection",))
        assert len(rejections) == 1
        assert rejections[0]["kind"] == "rejection"

    def test_missing_log_returns_empty(self, tmp_path: Path) -> None:
        assert read_audit(log_path=tmp_path / "nope.jsonl") == []
