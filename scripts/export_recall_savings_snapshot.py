"""Export the public recall-savings snapshot from raw local telemetry.

Writes benchmarks/recall_savings_snapshot.jsonl: whitelisted integer counts +
opaque ids only (see SNAPSHOT_FIELDS). The raw chunks.jsonl never ships.
Verify the public claim with:

    python3 scripts/recall_savings_report.py --snapshot benchmarks/recall_savings_snapshot.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from axon.observability.savings import aggregate_snapshot, export_savings_snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunks", type=Path, required=True, help="Raw chunks.jsonl path.")
    default_out = (
        Path(__file__).resolve().parents[1] / "benchmarks" / "recall_savings_snapshot.jsonl"
    )
    parser.add_argument("--out", type=Path, default=default_out)
    parser.add_argument(
        "--remap-json",
        type=Path,
        help="JSON dict of declared directory moves (old prefix -> new prefix), "
        "e.g. benchmarks/recall_savings_remap.json; a remap only applies when "
        "the file exists at the new location.",
    )
    args = parser.parse_args(argv)

    path_remaps = (
        json.loads(args.remap_json.read_text(encoding="utf-8")) if args.remap_json else None
    )
    rows = export_savings_snapshot(args.chunks, path_remaps=path_remaps)
    args.out.parent.mkdir(exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")

    aggregate = aggregate_snapshot(args.out)
    ratio = aggregate.savings_ratio
    print(
        f"wrote {args.out}: requests={aggregate.requests} "
        f"returned={aggregate.returned_tokens} "
        f"counterfactual={aggregate.counterfactual_tokens} "
        f"savings={ratio:.3f}"
        if ratio is not None
        else "savings=n/a"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
