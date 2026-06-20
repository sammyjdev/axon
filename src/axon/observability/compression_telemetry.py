from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from axon.config.runtime import RuntimeConfig, load_runtime_config


class CompressionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    ts: str
    engine: str  # e.g. "caveman/phi3+rtk", "caveman/phi3", "fallback"
    caller: str  # "claude-code", "codex", "cli"
    ctx: str | None
    before_tokens: int
    after_tokens: int
    reduction_tokens: int
    reduction_pct: float
    # "compression" = a real compression pipeline record;
    # "tool_io" = instrumented MCP graph/tool I/O (T-104 pollution).
    # Default keeps legacy JSON lines (which lack the field) parsing fine.
    kind: Literal["compression", "tool_io"] = "compression"


class CompressionTelemetryStore:
    def __init__(self, runtime: RuntimeConfig | None = None) -> None:
        self._runtime = runtime or load_runtime_config()
        self._file = self._runtime.data_root / "compression" / "stats.jsonl"

    @property
    def stats_file(self) -> Path:
        return self._file

    def append(self, record: CompressionRecord) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        with self._file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record.model_dump(), sort_keys=True) + "\n")

    def load_all(self) -> list[CompressionRecord]:
        if not self._file.exists():
            return []
        records = []
        with self._file.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    records.append(CompressionRecord(**json.loads(line)))
        return records

    def summary(self) -> dict:
        from axon.observability.gain import is_compression_record

        all_records = self.load_all()
        # Filter to compression-only records using the canonical predicate
        # (T-104): excludes tool_io records AND legacy pollution (engine is
        # a tool name not in the COMPRESSION_ENGINES allowlist).
        records = [r for r in all_records if is_compression_record(r)]
        by_engine: dict[str, int] = {}
        for r in records:
            by_engine[r.engine] = by_engine.get(r.engine, 0) + 1
        total_before = sum(r.before_tokens for r in records)
        total_after = sum(r.after_tokens for r in records)
        total_saved = sum(r.reduction_tokens for r in records)

        # Compute reduction statistics only over records where compression
        # actually ran (reduction_pct > 0). No-op records written by tools
        # like get_graph_path or by disabled engines (reduction_pct == 0)
        # would otherwise dilute the average to a meaningless number.
        compressed_pcts = sorted(r.reduction_pct for r in records if r.reduction_pct > 0)
        count_compressed = len(compressed_pcts)

        if count_compressed == 0:
            avg_pct: float | None = None
            p50: float | None = None
            p95: float | None = None
            max_pct: float | None = None
        else:
            avg_pct = round(sum(compressed_pcts) / count_compressed, 1)
            p50 = round(_percentile(compressed_pcts, 50), 1)
            p95 = round(_percentile(compressed_pcts, 95), 1)
            max_pct = round(compressed_pcts[-1], 1)

        return {
            "total_calls": len(records),
            "count_total": len(records),
            "count_compressed": count_compressed,
            "total_before_tokens": total_before,
            "total_after_tokens": total_after,
            "total_saved_tokens": total_saved,
            "avg_reduction_pct": avg_pct,
            "p50_reduction_pct": p50,
            "p95_reduction_pct": p95,
            "max_reduction_pct": max_pct,
            "by_engine": by_engine,
        }


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Linear-interpolated percentile over a pre-sorted list. No numpy."""
    if not sorted_values:
        raise ValueError("empty sequence")
    if len(sorted_values) == 1:
        return sorted_values[0]
    # Position on [0, n-1]
    pos = (pct / 100.0) * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


def main() -> None:
    """Print summary() as JSON for the committed stats.jsonl."""
    import json as _json
    import sys

    store = CompressionTelemetryStore()
    sys.stdout.write(_json.dumps(store.summary(), indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
