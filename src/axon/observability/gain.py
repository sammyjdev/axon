"""observability/gain.py — canonical compression-gain data layer for `axon gain`.

Exposes:
  - COMPRESSION_ENGINES: allowlist of real compression-pipeline engine names.
  - is_compression_record(): canonical predicate used by both this module and
    CompressionTelemetryStore.summary() to exclude T-104 pollution.
  - GainSummary: Pydantic v2 summary model.
  - compute_gain(): pure function over a list of CompressionRecord.
  - load_gain(): convenience loader that reads the live store then computes.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from pydantic import BaseModel

from axon.observability.compression_telemetry import (
    CompressionRecord,
    CompressionTelemetryStore,
    _percentile,
)

if TYPE_CHECKING:
    from axon.config.runtime import RuntimeConfig

# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------

# Covers all current and historical engine names produced by the compression
# pipeline.  Tool names (e.g. "get_graph_path") are intentionally absent so
# that both the kind=="tool_io" gate AND this engine-name gate each independently
# block pollution — defence in depth for legacy JSONL written before T-104.
COMPRESSION_ENGINES: frozenset[str] = frozenset(
    {
        "caveman/phi3+rtkx",
        "caveman/phi3+rtk",  # historical (pre-rtkx rebrand)
        "caveman/phi3",
        "rtkx",
        "rtk",  # historical
        "fallback",
        "disabled",
    }
)


# ---------------------------------------------------------------------------
# Canonical predicate
# ---------------------------------------------------------------------------


def is_compression_record(record: CompressionRecord) -> bool:
    """Return True iff *record* is a real compression-pipeline entry.

    A record is real when BOTH conditions hold:
      1. kind == "compression"  (new gate, T-104)
      2. engine is in COMPRESSION_ENGINES  (legacy gate — handles old JSONL
         written before the kind field existed, whose engine is a tool name)
    """
    return record.kind == "compression" and record.engine in COMPRESSION_ENGINES


# ---------------------------------------------------------------------------
# Summary model
# ---------------------------------------------------------------------------


class GainSummary(BaseModel):
    """Aggregated compression-gain statistics over a set of CompressionRecords."""

    windows: int
    """Number of compression records (post-filter)."""

    compressed: int
    """Subset with reduction_tokens > 0."""

    before_tokens: int
    """Sum of before_tokens over compression records."""

    after_tokens: int
    """Sum of after_tokens over compression records."""

    saved_tokens: int
    """Sum of reduction_tokens over compression records."""

    p50_pct: float | None
    """50th-percentile of reduction_pct over compressed>0 subset."""

    mean_pct: float | None
    """Mean of reduction_pct over compressed>0 subset."""

    p95_pct: float | None
    """95th-percentile of reduction_pct over compressed>0 subset."""

    max_pct: float | None
    """Maximum reduction_pct over compressed>0 subset."""

    by_engine: dict[str, int]
    """Engine -> count of compression records."""

    daily_saved: list[tuple[str, int]]
    """(YYYY-MM-DD, saved_tokens) sorted ascending; intended for sparklines."""


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------


def compute_gain(records: list[CompressionRecord]) -> GainSummary:
    """Compute GainSummary from an arbitrary list of CompressionRecords.

    Records that fail is_compression_record() are silently excluded so callers
    can pass load_all() directly without pre-filtering.
    """
    filtered = [r for r in records if is_compression_record(r)]

    windows = len(filtered)
    before_tokens = sum(r.before_tokens for r in filtered)
    after_tokens = sum(r.after_tokens for r in filtered)
    saved_tokens = sum(r.reduction_tokens for r in filtered)

    # Engine distribution
    by_engine: dict[str, int] = {}
    for r in filtered:
        by_engine[r.engine] = by_engine.get(r.engine, 0) + 1

    # Percentile stats — only over the subset where compression actually ran
    compressed_subset = sorted(
        r.reduction_pct for r in filtered if r.reduction_tokens > 0
    )
    compressed = len(compressed_subset)

    if compressed == 0:
        p50_pct: float | None = None
        mean_pct: float | None = None
        p95_pct: float | None = None
        max_pct: float | None = None
    else:
        p50_pct = round(_percentile(compressed_subset, 50), 1)
        mean_pct = round(sum(compressed_subset) / compressed, 1)
        p95_pct = round(_percentile(compressed_subset, 95), 1)
        max_pct = round(compressed_subset[-1], 1)

    # Daily bucketing — derive date from the ISO timestamp prefix
    daily_map: dict[str, int] = defaultdict(int)
    for r in filtered:
        # ts is an ISO 8601 string; the date is the first 10 characters.
        date_str = r.ts[:10]
        daily_map[date_str] += r.reduction_tokens
    daily_saved = sorted(daily_map.items())

    return GainSummary(
        windows=windows,
        compressed=compressed,
        before_tokens=before_tokens,
        after_tokens=after_tokens,
        saved_tokens=saved_tokens,
        p50_pct=p50_pct,
        mean_pct=mean_pct,
        p95_pct=p95_pct,
        max_pct=max_pct,
        by_engine=by_engine,
        daily_saved=daily_saved,
    )


# ---------------------------------------------------------------------------
# Convenience loader
# ---------------------------------------------------------------------------


def load_gain(runtime: RuntimeConfig | None = None) -> GainSummary:
    """Load all records from CompressionTelemetryStore and return GainSummary."""
    store = CompressionTelemetryStore(runtime)
    return compute_gain(store.load_all())
