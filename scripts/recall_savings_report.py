from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from axon.observability.savings import (
    METHOD,
    aggregate_recall_savings,
    aggregate_snapshot,
    format_ratio,
)


def _default_data_root() -> Path:
    raw = os.environ.get("AXON_DATA_ROOT")
    if raw:
        return Path(raw).expanduser()
    return Path.cwd() / "data"


def _default_chunks_file(data_root: Path) -> Path:
    return data_root / "recall" / "chunks.jsonl"


def build_report(file_path: Path, *, snapshot: bool = False) -> str:
    aggregate = aggregate_snapshot(file_path) if snapshot else aggregate_recall_savings(file_path)
    lines = [f"METHOD: {METHOD}"]
    for request in aggregate.request_rows:
        lines.append(
            "request "
            f"{request.query_hash} "
            f"returned={request.returned_tokens} "
            f"counterfactual={request.counterfactual_tokens} "
            f"savings_ratio={format_ratio(request.returned_tokens, request.counterfactual_tokens)} "
            f"missing_files={request.missing_files}"
        )

    lines.append(
        "aggregate "
        f"requests={aggregate.requests} "
        f"returned_tokens={aggregate.returned_tokens} "
        f"counterfactual_tokens={aggregate.counterfactual_tokens} "
        f"savings_ratio={format_ratio(aggregate.returned_tokens, aggregate.counterfactual_tokens)} "
        f"rows_skipped_no_file_path={aggregate.rows_skipped_no_file_path} "
        f"rows_skipped_missing_files={aggregate.rows_skipped_missing_files} "
        f"missing_file_refs={aggregate.missing_file_refs}"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report recall token savings versus reading source files in full."
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=_default_data_root(),
        help="AXON data root (default: AXON_DATA_ROOT or ./data).",
    )
    parser.add_argument(
        "--file",
        type=Path,
        help="Override chunks.jsonl path.",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        help="Report from a public snapshot (benchmarks/recall_savings_snapshot.jsonl) "
        "instead of raw local telemetry.",
    )
    args = parser.parse_args(argv)
    if args.snapshot:
        print(build_report(args.snapshot, snapshot=True))
        return 0
    target = args.file or _default_chunks_file(args.data_root)
    print(build_report(target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
