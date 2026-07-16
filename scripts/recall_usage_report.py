#!/usr/bin/env python3
"""Report which files are retrieved most often."""
from __future__ import annotations

import argparse
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from axon.observability.recall_telemetry import (  # noqa: E402
    ChunkRecord,
    RecallTelemetryStore,
)


@dataclass
class _Acc:
    count: int = 0
    queries: set[str] = field(default_factory=set)
    scores: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class UsageRow:
    file_path: str
    count: int
    distinct_queries: int
    mean_rank: float | None


def filter_since(records: list[ChunkRecord], since: str | None) -> list[ChunkRecord]:
    if since is None:
        return records
    return [record for record in records if record.ts >= since]


def aggregate_usage(
    records: list[ChunkRecord], top: int, since: str | None = None
) -> list[UsageRow]:
    usage: dict[str, _Acc] = {}
    for record in filter_since(records, since):
        for chunk in record.chunks:
            file_path = chunk.get("file_path") or "(unknown)"
            entry = usage.setdefault(file_path, _Acc())
            entry.count += 1
            entry.queries.add(record.query_hash)
            if chunk.get("ranking_score") is not None:
                entry.scores.append(chunk["ranking_score"])

    rows = [
        UsageRow(
            file_path=file_path,
            count=entry.count,
            distinct_queries=len(entry.queries),
            mean_rank=statistics.mean(entry.scores) if entry.scores else None,
        )
        for file_path, entry in usage.items()
    ]
    return sorted(rows, key=lambda row: row.count, reverse=True)[:top]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--since")
    parser.add_argument("--data-root", type=Path)
    args = parser.parse_args()

    store = (
        RecallTelemetryStore(runtime=SimpleNamespace(data_root=args.data_root))
        if args.data_root
        else RecallTelemetryStore()
    )
    window = filter_since(store.load_chunks(), args.since)
    print(f"records: {len(window)}")
    print("file_path  count  distinct_queries  mean_rank")
    for row in aggregate_usage(window, args.top):
        mean_rank = "-" if row.mean_rank is None else f"{row.mean_rank:.3f}"
        print(f"{row.file_path}  {row.count}  {row.distinct_queries}  {mean_rank}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
