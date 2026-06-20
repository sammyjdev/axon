"""Tests for compression_telemetry.summary().

The summary must reflect compression-only statistics (records where
reduction_pct > 0), not no-op records from instrumented tools that
write zero-reduction entries.

T-104: summary() now pre-filters via is_compression_record() so that:
  (a) records with kind="tool_io" are excluded
  (b) legacy records without a kind field but with a non-compression engine
      (e.g. engine="get_graph_path") are excluded by the engine allowlist
  (c) legitimate records with kind="compression" and a real engine are counted
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from axon.observability import compression_telemetry as ct
from axon.observability.compression_telemetry import (
    CompressionRecord,
    CompressionTelemetryStore,
)


def _make_store(tmp_path: Path) -> CompressionTelemetryStore:
    runtime = SimpleNamespace(data_root=tmp_path)
    return CompressionTelemetryStore(runtime=runtime)  # type: ignore[arg-type]


def _record(
    pct: float,
    before: int = 1000,
    after: int | None = None,
    engine: str = "caveman/phi3+rtk",
    kind: str = "compression",
) -> CompressionRecord:
    after = after if after is not None else int(before * (1 - pct / 100))
    return CompressionRecord(
        ts="2026-05-01T00:00:00+00:00",
        engine=engine,
        caller="cli",
        ctx="personal",
        before_tokens=before,
        after_tokens=after,
        reduction_tokens=before - after,
        reduction_pct=pct,
        kind=kind,  # type: ignore[arg-type]
    )


def test_empty_file_returns_zeros(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    s = store.summary()
    assert s["count_total"] == 0
    assert s["count_compressed"] == 0
    assert s["avg_reduction_pct"] is None
    assert s["p50_reduction_pct"] is None
    assert s["p95_reduction_pct"] is None
    assert s["max_reduction_pct"] is None
    assert s["by_engine"] == {}


def test_only_tool_io_records_excluded(tmp_path: Path) -> None:
    """(a) tool_io records are fully excluded from summary — count_total == 0."""
    store = _make_store(tmp_path)
    for _ in range(5):
        store.append(_record(0.0, before=100, after=100, engine="get_graph_path", kind="tool_io"))
    s = store.summary()
    assert s["count_total"] == 0
    assert s["count_compressed"] == 0
    assert s["avg_reduction_pct"] is None
    assert s["by_engine"] == {}


def test_legacy_non_compression_engine_excluded(tmp_path: Path) -> None:
    """(b) Legacy records without kind but with a tool-name engine are excluded.

    We simulate legacy JSONL by writing a record with kind="compression" but
    engine set to a tool name (not in COMPRESSION_ENGINES).  The engine-name
    gate independently blocks these regardless of kind.
    """
    store = _make_store(tmp_path)
    # Write raw JSONL without the kind field — simulates a record from before T-104
    record_dict = {
        "ts": "2026-04-01T00:00:00+00:00",
        "engine": "get_graph_path",  # tool name, not in COMPRESSION_ENGINES
        "caller": "mcp",
        "ctx": None,
        "before_tokens": 500,
        "after_tokens": 500,
        "reduction_tokens": 0,
        "reduction_pct": 0.0,
        # no "kind" key — legacy
    }
    store.stats_file.parent.mkdir(parents=True, exist_ok=True)
    with store.stats_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record_dict, sort_keys=True) + "\n")

    s = store.summary()
    assert s["count_total"] == 0
    assert s["by_engine"] == {}


def test_legitimate_records_counted(tmp_path: Path) -> None:
    """(c) Legitimate compression records with real engines are included."""
    store = _make_store(tmp_path)
    store.append(_record(55.0, engine="caveman/phi3+rtkx", kind="compression"))
    store.append(_record(30.0, engine="rtkx", kind="compression"))
    # Add a tool_io polluter — should not appear
    store.append(_record(0.0, engine="get_graph_neighbors", kind="tool_io"))

    s = store.summary()
    assert s["count_total"] == 2
    assert s["count_compressed"] == 2
    assert "caveman/phi3+rtkx" in s["by_engine"]
    assert "rtkx" in s["by_engine"]
    assert "get_graph_neighbors" not in s["by_engine"]


def test_mixed_records_compute_compressed_only(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    # 3 tool_io polluters (formerly written by _record_mcp_tool_call)
    for _ in range(3):
        store.append(_record(0.0, before=50, after=50, engine="get_graph_path", kind="tool_io"))
    # Compressed values, deliberately out of order:
    pcts = [91.6, 52.6, 30.0, 70.0, 55.0]
    for p in pcts:
        store.append(_record(p))

    s = store.summary()
    # Only the 5 real compression records survive the filter
    assert s["count_total"] == 5
    assert s["count_compressed"] == 5

    # Hand-computed against sorted [30.0, 52.6, 55.0, 70.0, 91.6]
    # mean = 299.2 / 5 = 59.84 -> rounded 1dp = 59.8
    # p50 with linear interp at pos = 0.5 * 4 = 2 -> 55.0
    # p95 at pos = 0.95 * 4 = 3.8 -> 70.0 + 0.8 * (91.6 - 70.0) = 87.28 -> 87.3
    # max = 91.6
    assert s["avg_reduction_pct"] == 59.8
    assert s["p50_reduction_pct"] == 55.0
    assert s["p95_reduction_pct"] == 87.3
    assert s["max_reduction_pct"] == 91.6
    assert "get_graph_path" not in s["by_engine"]
    assert s["by_engine"]["caveman/phi3+rtk"] == 5


def test_percentile_helper_single_value() -> None:
    assert ct._percentile([42.0], 50) == 42.0
    assert ct._percentile([42.0], 95) == 42.0


def test_percentile_helper_linear_interp() -> None:
    # Sorted [0, 10, 20, 30, 40]. p50 at pos 2 -> 20.
    assert ct._percentile([0.0, 10.0, 20.0, 30.0, 40.0], 50) == 20.0
    # p25 at pos 1 -> 10. p75 at pos 3 -> 30.
    assert ct._percentile([0.0, 10.0, 20.0, 30.0, 40.0], 25) == 10.0
    assert ct._percentile([0.0, 10.0, 20.0, 30.0, 40.0], 75) == 30.0


def test_load_all_roundtrip(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.append(_record(50.0))
    raw = store.stats_file.read_text(encoding="utf-8").strip()
    parsed = json.loads(raw)
    assert parsed["reduction_pct"] == 50.0
    recs = store.load_all()
    assert len(recs) == 1
    assert recs[0].reduction_pct == 50.0
