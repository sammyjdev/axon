from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

METHOD = (
    "counterfactual = reading each source file in full (Read/grep workflow); "
    "telemetry rows without file_path (pre-T8) are excluded"
)


@dataclass(frozen=True)
class SavingsRequest:
    query_hash: str
    returned_tokens: int
    counterfactual_tokens: int
    missing_files: int


@dataclass(frozen=True)
class SavingsAggregate:
    requests: int = 0
    returned_tokens: int = 0
    counterfactual_tokens: int = 0
    rows_skipped_no_file_path: int = 0
    rows_skipped_missing_files: int = 0
    missing_file_refs: int = 0
    request_rows: list[SavingsRequest] = field(default_factory=list)

    @property
    def savings_ratio(self) -> float | None:
        if self.counterfactual_tokens <= 0:
            return None
        return 1 - (self.returned_tokens / self.counterfactual_tokens)


def format_ratio(returned_tokens: int, counterfactual_tokens: int) -> str:
    if counterfactual_tokens <= 0:
        return "n/a"
    return f"{1 - (returned_tokens / counterfactual_tokens):.4f}"


def _lines(file_path: Path, max_lines: int | None) -> Iterable[str]:
    if max_lines is None:
        yield from file_path.read_text(encoding="utf-8").splitlines()
        return

    with file_path.open(encoding="utf-8") as fh:
        yield from deque(fh, maxlen=max_lines)


def aggregate_recall_savings(file_path: Path, *, max_lines: int | None = None) -> SavingsAggregate:
    if not file_path.exists():
        return SavingsAggregate()

    requests = 0
    returned_total = 0
    counterfactual_total = 0
    rows_skipped_no_file_path = 0
    rows_skipped_missing_files = 0
    missing_file_refs = 0
    request_rows: list[SavingsRequest] = []

    for raw_line in _lines(file_path, max_lines):
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
        request_rows.append(
            SavingsRequest(
                query_hash=str(record.get("query_hash", "?")),
                returned_tokens=returned_tokens,
                counterfactual_tokens=counterfactual_tokens,
                missing_files=missing_here,
            )
        )

    return SavingsAggregate(
        requests=requests,
        returned_tokens=returned_total,
        counterfactual_tokens=counterfactual_total,
        rows_skipped_no_file_path=rows_skipped_no_file_path,
        rows_skipped_missing_files=rows_skipped_missing_files,
        missing_file_refs=missing_file_refs,
        request_rows=request_rows,
    )
