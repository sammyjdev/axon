"""Tests for compression_telemetry.summary().

The summary must reflect compression-only statistics (records where
reduction_pct > 0), not no-op records from instrumented tools that
write zero-reduction entries.
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


def test_only_zero_reduction_records(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    for _ in range(5):
        store.append(_record(0.0, before=100, after=100, engine="get_graph_path"))
    s = store.summary()
    assert s["count_total"] == 5
    assert s["count_compressed"] == 0
    assert s["avg_reduction_pct"] is None
    assert s["p50_reduction_pct"] is None
    assert s["max_reduction_pct"] is None
    assert s["by_engine"] == {"get_graph_path": 5}


def test_mixed_records_compute_compressed_only(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    # 3 no-ops + 5 real compression events
    for _ in range(3):
        store.append(_record(0.0, before=50, after=50, engine="get_graph_path"))
    # Compressed values, deliberately out of order:
    pcts = [91.6, 52.6, 30.0, 70.0, 55.0]
    for p in pcts:
        store.append(_record(p))

    s = store.summary()
    assert s["count_total"] == 8
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
    assert s["by_engine"]["get_graph_path"] == 3
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
