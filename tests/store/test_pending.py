"""Tests for axon.store.pending — pending dir + drain + quarantine (dec-112)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from axon.store.pending import (
    PendingPaths,
    drain_pending,
    quarantine_invalid,
    write_pending,
)


@pytest.fixture
def paths(tmp_path: Path) -> PendingPaths:
    return PendingPaths(
        pending_dir=tmp_path / "pending",
        quarantine_dir=tmp_path / "pending-quarantine",
        quarantine_log=tmp_path / "quarantine.jsonl",
    )


@pytest.mark.asyncio
class TestWritePending:
    async def test_write_creates_file_with_unique_path(self, paths: PendingPaths) -> None:
        path = await write_pending(
            payload={"kind": "code_change", "commit_hash": "abc123", "data": {}},
            commit_hash="abc123",
            paths=paths,
        )
        assert path.exists()
        assert path.parent == paths.pending_dir
        assert path.name.startswith("abc123-")
        assert path.suffix == ".json"
        # Payload round-trips
        loaded = json.loads(path.read_text())
        assert loaded["commit_hash"] == "abc123"

    async def test_multiple_writes_to_same_commit_hash_produce_unique_files(
        self, paths: PendingPaths
    ) -> None:
        p1 = await write_pending(payload={"k": 1}, commit_hash="abc", paths=paths)
        p2 = await write_pending(payload={"k": 2}, commit_hash="abc", paths=paths)
        assert p1 != p2
        assert p1.exists() and p2.exists()

    async def test_write_creates_pending_dir_if_missing(self, paths: PendingPaths) -> None:
        assert not paths.pending_dir.exists()
        await write_pending(payload={}, commit_hash="x", paths=paths)
        assert paths.pending_dir.exists()


@pytest.mark.asyncio
class TestDrainPending:
    async def test_drain_empty_returns_zero_processed(self, paths: PendingPaths) -> None:
        paths.pending_dir.mkdir(parents=True, exist_ok=True)
        result = await drain_pending(paths, sink=lambda payload: _async_noop())
        assert result.processed == 0
        assert result.quarantined == 0
        assert result.retried == 0

    async def test_drain_processes_valid_payloads_in_chronological_order(
        self, paths: PendingPaths
    ) -> None:
        seen: list[dict] = []

        async def sink(payload: dict) -> None:
            seen.append(payload)

        await write_pending(payload={"order": 1}, commit_hash="a", paths=paths)
        await write_pending(payload={"order": 2}, commit_hash="b", paths=paths)
        await write_pending(payload={"order": 3}, commit_hash="c", paths=paths)

        result = await drain_pending(paths, sink=sink)
        assert result.processed == 3
        assert [s["order"] for s in seen] == [1, 2, 3]
        # Files removed after successful sink
        assert list(paths.pending_dir.glob("*.json")) == []

    async def test_drain_quarantines_malformed_json(self, paths: PendingPaths) -> None:
        paths.pending_dir.mkdir(parents=True, exist_ok=True)
        bad = paths.pending_dir / "bad-123.json"
        bad.write_text("{not valid json")

        async def sink(payload: dict) -> None:
            raise AssertionError("should not be called for malformed file")

        result = await drain_pending(paths, sink=sink)
        assert result.quarantined == 1
        assert result.processed == 0
        # File moved to quarantine
        assert not bad.exists()
        assert any(paths.quarantine_dir.glob("bad-123.json*"))
        # quarantine.jsonl records the entry
        log_lines = paths.quarantine_log.read_text().strip().splitlines()
        assert len(log_lines) == 1
        entry = json.loads(log_lines[0])
        assert entry["original_path"].endswith("bad-123.json")
        assert "JSON" in entry["reason"] or "decode" in entry["reason"].lower()

    async def test_drain_leaves_file_in_place_on_retryable_sink_error(
        self, paths: PendingPaths
    ) -> None:
        await write_pending(payload={"x": 1}, commit_hash="a", paths=paths)

        class _Busy(Exception):
            pass

        async def sink(payload: dict) -> None:
            raise _Busy("database is locked")

        result = await drain_pending(
            paths,
            sink=sink,
            is_retryable=lambda e: isinstance(e, _Busy),
        )
        assert result.retried == 1
        assert result.processed == 0
        # File stays in pending
        assert list(paths.pending_dir.glob("*.json")) != []

    async def test_drain_continues_loop_after_quarantined_file(
        self, paths: PendingPaths
    ) -> None:
        # Mix: one bad file, then one good file
        paths.pending_dir.mkdir(parents=True, exist_ok=True)
        bad = paths.pending_dir / "aaa-1.json"
        bad.write_text("garbage")
        await write_pending(payload={"ok": True}, commit_hash="bbb", paths=paths)

        seen: list[dict] = []

        async def sink(payload: dict) -> None:
            seen.append(payload)

        result = await drain_pending(paths, sink=sink)
        # One quarantined, one processed
        assert result.quarantined == 1
        assert result.processed == 1
        assert seen == [{"ok": True}]


@pytest.mark.asyncio
class TestQuarantineInvalid:
    async def test_quarantine_moves_file_and_logs(self, paths: PendingPaths) -> None:
        paths.pending_dir.mkdir(parents=True, exist_ok=True)
        bad = paths.pending_dir / "weird.json"
        bad.write_text("nope")
        await quarantine_invalid(bad, reason="ParseError: bad token", paths=paths)
        assert not bad.exists()
        assert any(paths.quarantine_dir.iterdir())
        log = paths.quarantine_log.read_text().strip().splitlines()
        assert len(log) == 1
        entry = json.loads(log[0])
        assert "ParseError" in entry["reason"]


async def _async_noop(*_: object, **__: object) -> None:
    return None
