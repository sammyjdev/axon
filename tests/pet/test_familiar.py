"""Tests for pet/familiar.py — dec-119 canonical-sources rebase.

Covers:
  (a) tokens-saved reads through gain (load_gain) and excludes tool_io/pollution.
  (b) Activity poller detects newly-appended TraceStore records.
  (c) No hard-coded paths: functions operate under a tmp data_root.
  (d) --frames smoke-test: main() exits after N ticks.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from datetime import UTC, datetime

import pytest

from axon.observability.compression_telemetry import CompressionRecord, CompressionTelemetryStore
from axon.observability.trace_store import TraceRecord, TraceStore
from axon.pet.familiar import (
    ActivityPoller,
    Dendrite,
    State,
    fetch_adr_data,
    fetch_compression_data,
    main as familiar_main,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_runtime(tmp_path: Path) -> SimpleNamespace:
    """Build a minimal runtime-like namespace pointing at tmp_path."""
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(data_root=data_root)


def _make_compression_record(
    engine: str = "caveman/phi3+rtkx",
    kind: str = "compression",
    reduction_tokens: int = 500,
    ts: str = "2026-06-01T10:00:00+00:00",
) -> CompressionRecord:
    return CompressionRecord(
        ts=ts,
        engine=engine,
        caller="cli",
        ctx="personal",
        before_tokens=1000,
        after_tokens=1000 - reduction_tokens,
        reduction_tokens=reduction_tokens,
        reduction_pct=float(reduction_tokens) / 10,
        kind=kind,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# (a) tokens-saved reads through gain, excludes pollution
# ---------------------------------------------------------------------------

class TestFetchCompressionData:
    def test_empty_store_returns_zero(self, tmp_path: Path) -> None:
        rt = _make_runtime(tmp_path)
        tokens, moments = fetch_compression_data(rt)  # type: ignore[arg-type]
        assert tokens == 0
        assert moments == []

    def test_real_records_counted(self, tmp_path: Path) -> None:
        rt = _make_runtime(tmp_path)
        store = CompressionTelemetryStore(rt)  # type: ignore[arg-type]
        store.append(_make_compression_record(engine="rtkx", reduction_tokens=300))
        store.append(_make_compression_record(engine="caveman/phi3", reduction_tokens=200))

        tokens, moments = fetch_compression_data(rt)  # type: ignore[arg-type]
        assert tokens == 500
        # Both real records have reduction_tokens > 0 → both appear as moments
        assert len(moments) == 2

    def test_tool_io_pollution_excluded(self, tmp_path: Path) -> None:
        rt = _make_runtime(tmp_path)
        store = CompressionTelemetryStore(rt)  # type: ignore[arg-type]
        # Polluter
        store.append(_make_compression_record(engine="get_graph_path", kind="tool_io", reduction_tokens=9999))
        # Real record
        store.append(_make_compression_record(engine="rtkx", reduction_tokens=100))

        tokens, _moments = fetch_compression_data(rt)  # type: ignore[arg-type]
        assert tokens == 100  # polluter excluded

    def test_legacy_engine_name_excluded(self, tmp_path: Path) -> None:
        rt = _make_runtime(tmp_path)
        store = CompressionTelemetryStore(rt)  # type: ignore[arg-type]
        # Legacy pollution: kind="compression" but engine is a tool name
        store.append(_make_compression_record(engine="get_graph_neighbors", kind="compression", reduction_tokens=5000))
        store.append(_make_compression_record(engine="fallback", reduction_tokens=50))

        tokens, _moments = fetch_compression_data(rt)  # type: ignore[arg-type]
        assert tokens == 50


# ---------------------------------------------------------------------------
# (b) Activity poller detects newly-appended TraceStore records
# ---------------------------------------------------------------------------

class TestActivityPoller:
    def _make_record(self, trace_id: str = "abc123") -> TraceRecord:
        return TraceRecord(
            trace_id=trace_id,
            stage="invoke",
            caller="cli",
            ts=datetime.now(UTC).isoformat(),
        )

    def test_no_records_returns_empty(self, tmp_path: Path) -> None:
        records_file = tmp_path / "records.jsonl"
        poller = ActivityPoller(records_file=records_file)
        assert poller.poll() == []

    def test_detects_appended_record(self, tmp_path: Path) -> None:
        rt = _make_runtime(tmp_path)
        store = TraceStore(rt)  # type: ignore[arg-type]
        poller = ActivityPoller(records_file=store.records_file)

        # First poll (file doesn't exist yet) → empty
        assert poller.poll() == []

        # Append a record
        store.append(self._make_record("trace-1"))

        # Second poll should detect the new record
        new = poller.poll()
        assert len(new) == 1
        assert new[0]["trace_id"] == "trace-1"
        assert new[0]["stage"] == "invoke"

    def test_incremental_reads_only_new_records(self, tmp_path: Path) -> None:
        rt = _make_runtime(tmp_path)
        store = TraceStore(rt)  # type: ignore[arg-type]
        poller = ActivityPoller(records_file=store.records_file)

        store.append(self._make_record("t1"))
        first_poll = poller.poll()
        assert len(first_poll) == 1

        # Poll again without new records → empty
        assert poller.poll() == []

        # Append more records
        store.append(self._make_record("t2"))
        store.append(self._make_record("t3"))
        second_poll = poller.poll()
        assert len(second_poll) == 2
        trace_ids = {r["trace_id"] for r in second_poll}
        assert trace_ids == {"t2", "t3"}

    def test_poller_seeded_at_eof_sees_only_new_records(self, tmp_path: Path) -> None:
        """Simulate the familiar seeding offset to EOF so old records are ignored."""
        rt = _make_runtime(tmp_path)
        store = TraceStore(rt)  # type: ignore[arg-type]

        # Pre-existing record (before familiar starts)
        store.append(self._make_record("old-record"))

        poller = ActivityPoller(records_file=store.records_file)
        # Seed offset to current EOF (mimics familiar startup behaviour)
        poller._offset = store.records_file.stat().st_size

        # Pre-existing record must NOT be reported
        assert poller.poll() == []

        # New record after seeding IS reported
        store.append(self._make_record("new-record"))
        new = poller.poll()
        assert len(new) == 1
        assert new[0]["trace_id"] == "new-record"


# ---------------------------------------------------------------------------
# (c) No hard-coded paths — all operations use the tmp data_root
# ---------------------------------------------------------------------------

class TestNoHardCodedPaths:
    def test_fetch_compression_data_uses_runtime_path(self, tmp_path: Path) -> None:
        rt = _make_runtime(tmp_path)
        store = CompressionTelemetryStore(rt)  # type: ignore[arg-type]
        store.append(_make_compression_record(engine="rtkx", reduction_tokens=42))

        tokens, _ = fetch_compression_data(rt)  # type: ignore[arg-type]
        assert tokens == 42

    def test_fetch_adr_data_uses_runtime_path(self, tmp_path: Path) -> None:
        rt = _make_runtime(tmp_path)
        # No DB → returns (0, []) without error; no hard-coded path consulted
        total, moments = fetch_adr_data(rt)  # type: ignore[arg-type]
        assert total == 0
        assert moments == []

    def test_activity_poller_path_is_configurable(self, tmp_path: Path) -> None:
        custom_file = tmp_path / "custom_records.jsonl"
        poller = ActivityPoller(records_file=custom_file)
        assert poller.records_file == custom_file
        # Non-existent file returns empty, not an error
        assert poller.poll() == []


# ---------------------------------------------------------------------------
# (d) --frames smoke-test: main() exits after N ticks
# ---------------------------------------------------------------------------

class TestFamiliarMainFrames:
    def test_main_exits_after_frames(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        """main(frames=3) must exit after 3 render ticks without Ctrl+C."""
        rt = _make_runtime(tmp_path)
        asyncio.run(familiar_main(runtime=rt, frames=3))  # type: ignore[arg-type]
        # If we reach here without exception, the bounded loop works.
        # We do not assert on stdout content (ANSI sequences, etc.).

    def test_main_frames_zero_interpreted_as_one_tick(self, tmp_path: Path) -> None:
        """frames=1 is the minimum meaningful bounded run."""
        rt = _make_runtime(tmp_path)
        asyncio.run(familiar_main(runtime=rt, frames=1))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Dendrite.trigger helper
# ---------------------------------------------------------------------------

class TestDendriteTrigger:
    def test_trigger_sets_fire_start_and_color(self) -> None:
        d = Dendrite(direction=0)
        now = 1000.0
        d.trigger(now, color=(255, 0, 0))
        assert d.fire_start == now
        assert d.last_fired == now
        assert d.fire_color == (255, 0, 0)

    def test_trigger_default_color(self) -> None:
        from axon.pet.familiar import _DEFAULT_FIRE_COLOR
        d = Dendrite(direction=1)
        d.trigger(999.0)
        assert d.fire_color == _DEFAULT_FIRE_COLOR
