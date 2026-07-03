from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

_METHOD = (
    "counterfactual = reading each source file in full (Read/grep workflow); "
    "telemetry rows without file_path (pre-T8) are excluded"
)


def _default_data_root() -> Path:
    raw = os.environ.get("AXON_DATA_ROOT")
    if raw:
        return Path(raw).expanduser()
    return Path.cwd() / "data"


def _default_chunks_file(data_root: Path) -> Path:
    return data_root / "recall" / "chunks.jsonl"


def _format_ratio(returned_tokens: int, counterfactual_tokens: int) -> str:
    if counterfactual_tokens <= 0:
        return "n/a"
    return f"{1 - (returned_tokens / counterfactual_tokens):.4f}"


def build_report(file_path: Path) -> str:
    requests = 0
    returned_total = 0
    counterfactual_total = 0
    rows_skipped_no_file_path = 0
    rows_skipped_missing_files = 0
    missing_file_refs = 0
    lines = [f"METHOD: {_METHOD}"]

    if not file_path.exists():
        lines.append(
            "aggregate requests=0 returned_tokens=0 counterfactual_tokens=0 "
            "savings_ratio=n/a rows_skipped_no_file_path=0 "
            "rows_skipped_missing_files=0 missing_file_refs=0"
        )
        return "\n".join(lines)

    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        record = json.loads(raw_line)
        chunks = record.get("chunks") or []
        if not chunks or any(not chunk.get("file_path") for chunk in chunks):
            rows_skipped_no_file_path += 1
            continue

        unique_paths = sorted({Path(str(chunk["file_path"])) for chunk in chunks})
        existing_paths = [path for path in unique_paths if path.exists()]
        missing_here = len(unique_paths) - len(existing_paths)
        missing_file_refs += missing_here
        if not existing_paths:
            rows_skipped_missing_files += 1
            continue

        returned_tokens = sum(int(chunk.get("token_estimate", 0)) for chunk in chunks)
        counterfactual_tokens = sum(
            len(path.read_text(encoding="utf-8")) // 4 for path in existing_paths
        )
        requests += 1
        returned_total += returned_tokens
        counterfactual_total += counterfactual_tokens
        lines.append(
            "request "
            f"{record.get('query_hash', '?')} "
            f"returned={returned_tokens} "
            f"counterfactual={counterfactual_tokens} "
            f"savings_ratio={_format_ratio(returned_tokens, counterfactual_tokens)} "
            f"missing_files={missing_here}"
        )

    lines.append(
        "aggregate "
        f"requests={requests} "
        f"returned_tokens={returned_total} "
        f"counterfactual_tokens={counterfactual_total} "
        f"savings_ratio={_format_ratio(returned_total, counterfactual_total)} "
        f"rows_skipped_no_file_path={rows_skipped_no_file_path} "
        f"rows_skipped_missing_files={rows_skipped_missing_files} "
        f"missing_file_refs={missing_file_refs}"
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
    args = parser.parse_args(argv)
    target = args.file or _default_chunks_file(args.data_root)
    print(build_report(target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
