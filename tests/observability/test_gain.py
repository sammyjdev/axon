"""Tests for observability/gain.py — the data layer for `axon gain`.

Covers:
  - empty store (all zeros / Nones, empty lists)
  - pollution excluded (tool_io records and legacy engine-name pollution)
  - daily bucketing (daily_saved aggregation)
  - percentile correctness (p50, p95)
  - by_engine counts
  - saved_tokens sum
"""

from __future__ import annotations

from axon.observability.compression_telemetry import CompressionRecord
from axon.observability.gain import (
    COMPRESSION_ENGINES,
    GainSummary,
    compute_gain,
    is_compression_record,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rec(
    pct: float,
    before: int = 1000,
    reduction: int | None = None,
    engine: str = "caveman/phi3+rtkx",
    kind: str = "compression",
    ts: str = "2026-05-01T10:00:00+00:00",
) -> CompressionRecord:
    red = reduction if reduction is not None else int(before * pct / 100)
    after = before - red
    return CompressionRecord(
        ts=ts,
        engine=engine,
        caller="cli",
        ctx="personal",
        before_tokens=before,
        after_tokens=after,
        reduction_tokens=red,
        reduction_pct=pct,
        kind=kind,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# COMPRESSION_ENGINES allowlist
# ---------------------------------------------------------------------------


def test_compression_engines_contains_all_expected() -> None:
    required = {
        "caveman/phi3+rtkx",
        "caveman/phi3+rtk",
        "caveman/phi3",
        "rtkx",
        "rtk",
        "fallback",
        "disabled",
    }
    assert required <= COMPRESSION_ENGINES


# ---------------------------------------------------------------------------
# is_compression_record predicate
# ---------------------------------------------------------------------------


def test_is_compression_record_real_engine() -> None:
    r = _rec(50.0, engine="caveman/phi3+rtkx", kind="compression")
    assert is_compression_record(r) is True


def test_is_compression_record_tool_io_excluded() -> None:
    r = _rec(0.0, engine="get_graph_path", kind="tool_io")
    assert is_compression_record(r) is False


def test_is_compression_record_legacy_tool_name_excluded() -> None:
    """Legacy JSONL where kind defaults to 'compression' but engine is a tool name."""
    r = _rec(0.0, engine="get_graph_neighbors", kind="compression")
    assert is_compression_record(r) is False


def test_is_compression_record_all_real_engines_pass() -> None:
    for engine in COMPRESSION_ENGINES:
        r = _rec(10.0, engine=engine, kind="compression")
        assert is_compression_record(r), f"engine={engine!r} should pass"


# ---------------------------------------------------------------------------
# Empty store
# ---------------------------------------------------------------------------


def test_compute_gain_empty_list() -> None:
    s = compute_gain([])
    assert s.windows == 0
    assert s.compressed == 0
    assert s.before_tokens == 0
    assert s.after_tokens == 0
    assert s.saved_tokens == 0
    assert s.p50_pct is None
    assert s.mean_pct is None
    assert s.p95_pct is None
    assert s.max_pct is None
    assert s.by_engine == {}
    assert s.daily_saved == []


def test_compute_gain_all_pollution() -> None:
    """All records are pollution — result should be identical to empty."""
    records = [
        _rec(0.0, engine="get_graph_path", kind="tool_io"),
        _rec(0.0, engine="restore_context", kind="tool_io"),
        _rec(0.0, engine="get_graph_context", kind="tool_io"),
    ]
    s = compute_gain(records)
    assert s.windows == 0
    assert s.saved_tokens == 0
    assert s.by_engine == {}
    assert s.daily_saved == []


# ---------------------------------------------------------------------------
# Pollution excluded
# ---------------------------------------------------------------------------


def test_pollution_excluded_from_counts() -> None:
    records = [
        _rec(55.0, engine="rtkx", kind="compression"),
        _rec(0.0, engine="get_graph_path", kind="tool_io"),   # polluter
        _rec(30.0, engine="fallback", kind="compression"),
    ]
    s = compute_gain(records)
    assert s.windows == 2
    assert "get_graph_path" not in s.by_engine
    assert s.by_engine == {"rtkx": 1, "fallback": 1}


def test_legacy_tool_engine_excluded() -> None:
    """kind='compression' but engine is a tool name — blocked by engine allowlist."""
    records = [
        _rec(50.0, engine="get_graph_neighbors", kind="compression"),  # legacy pollution
        _rec(40.0, engine="caveman/phi3", kind="compression"),
    ]
    s = compute_gain(records)
    assert s.windows == 1
    assert s.by_engine == {"caveman/phi3": 1}


# ---------------------------------------------------------------------------
# saved_tokens sum
# ---------------------------------------------------------------------------


def test_saved_tokens_sum() -> None:
    records = [
        _rec(50.0, before=1000, reduction=500, engine="rtkx"),
        _rec(25.0, before=800, reduction=200, engine="fallback"),
        _rec(0.0, engine="get_graph_path", kind="tool_io"),  # excluded
    ]
    s = compute_gain(records)
    assert s.saved_tokens == 700
    assert s.before_tokens == 1800
    assert s.after_tokens == 1100


# ---------------------------------------------------------------------------
# by_engine counts
# ---------------------------------------------------------------------------


def test_by_engine_counts() -> None:
    records = [
        _rec(50.0, engine="rtkx"),
        _rec(60.0, engine="rtkx"),
        _rec(40.0, engine="caveman/phi3"),
        _rec(0.0, engine="get_graph_path", kind="tool_io"),
    ]
    s = compute_gain(records)
    assert s.by_engine["rtkx"] == 2
    assert s.by_engine["caveman/phi3"] == 1
    assert "get_graph_path" not in s.by_engine


# ---------------------------------------------------------------------------
# Percentile correctness
# ---------------------------------------------------------------------------


def test_percentiles_five_values() -> None:
    # reduction_pct values (after filter): 30, 52.6, 55, 70, 91.6
    # All have reduction_tokens > 0, so all qualify for the percentile subset.
    records = [
        _rec(91.6, before=1000, reduction=916),
        _rec(52.6, before=1000, reduction=526),
        _rec(30.0, before=1000, reduction=300),
        _rec(70.0, before=1000, reduction=700),
        _rec(55.0, before=1000, reduction=550),
    ]
    s = compute_gain(records)
    assert s.compressed == 5
    # Sorted: [30.0, 52.6, 55.0, 70.0, 91.6]
    # p50 at pos 0.5*4=2 -> 55.0
    assert s.p50_pct == 55.0
    # mean = (30+52.6+55+70+91.6)/5 = 299.2/5 = 59.84 -> 59.8
    assert s.mean_pct == 59.8
    # p95 at pos 0.95*4=3.8 -> 70 + 0.8*(91.6-70) = 70+17.28 = 87.28 -> 87.3
    assert s.p95_pct == 87.3
    assert s.max_pct == 91.6


def test_percentiles_none_when_no_compressed_records() -> None:
    """Records exist but none have reduction_tokens > 0."""
    records = [
        _rec(0.0, before=500, reduction=0, engine="disabled"),
    ]
    s = compute_gain(records)
    assert s.windows == 1
    assert s.compressed == 0
    assert s.p50_pct is None
    assert s.mean_pct is None
    assert s.p95_pct is None
    assert s.max_pct is None


def test_percentiles_single_compressed_record() -> None:
    records = [_rec(42.0, before=1000, reduction=420)]
    s = compute_gain(records)
    assert s.p50_pct == 42.0
    assert s.p95_pct == 42.0
    assert s.max_pct == 42.0
    assert s.mean_pct == 42.0


# ---------------------------------------------------------------------------
# Daily bucketing
# ---------------------------------------------------------------------------


def test_daily_saved_bucketing() -> None:
    records = [
        _rec(50.0, before=1000, reduction=500, ts="2026-05-01T08:00:00+00:00"),
        _rec(30.0, before=800, reduction=240, ts="2026-05-01T22:00:00+00:00"),
        _rec(70.0, before=1200, reduction=840, ts="2026-05-03T10:00:00+00:00"),
        # polluter — should not contribute to daily_saved
        _rec(0.0, before=100, reduction=0, engine="get_graph_path", kind="tool_io",
             ts="2026-05-02T00:00:00+00:00"),
    ]
    s = compute_gain(records)
    # Expected: 2026-05-01 -> 740, 2026-05-03 -> 840 (sorted ascending)
    assert s.daily_saved == [("2026-05-01", 740), ("2026-05-03", 840)]


def test_daily_saved_empty_when_no_records() -> None:
    s = compute_gain([])
    assert s.daily_saved == []


def test_daily_saved_sorted_ascending() -> None:
    records = [
        _rec(40.0, before=1000, reduction=400, ts="2026-06-10T00:00:00+00:00"),
        _rec(40.0, before=1000, reduction=400, ts="2026-05-01T00:00:00+00:00"),
        _rec(40.0, before=1000, reduction=400, ts="2026-06-01T00:00:00+00:00"),
    ]
    s = compute_gain(records)
    dates = [d for d, _ in s.daily_saved]
    assert dates == sorted(dates)


# ---------------------------------------------------------------------------
# GainSummary model validation
# ---------------------------------------------------------------------------


def test_gain_summary_is_pydantic_model() -> None:
    s = compute_gain([])
    assert isinstance(s, GainSummary)
    # Verify JSON round-trip works (pydantic v2)
    dumped = s.model_dump()
    assert dumped["windows"] == 0
    assert dumped["daily_saved"] == []
