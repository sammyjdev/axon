from __future__ import annotations

import json
from pathlib import Path

from axon.observability.savings import aggregate_recall_savings


def _write_record(chunks_file: Path, *, query_hash: str, chunks: list[dict[str, object]]) -> None:
    with chunks_file.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "ts": "2026-07-03T00:00:00+00:00",
                    "query_hash": query_hash,
                    "strategy": "balanced",
                    "requested_max_tokens": 2000,
                    "chunks": chunks,
                }
            )
            + "\n"
        )


def test_aggregate_recall_savings_matches_report_fixture_math(tmp_path: Path) -> None:
    shared = tmp_path / "shared.py"
    shared.write_text("x" * 40, encoding="utf-8")
    extra = tmp_path / "extra.py"
    extra.write_text("y" * 20, encoding="utf-8")
    chunks_file = tmp_path / "chunks.jsonl"

    _write_record(
        chunks_file,
        query_hash="req-1",
        chunks=[
            {"hash": "a1", "file_path": str(shared), "token_estimate": 6},
            {"hash": "a2", "file_path": str(shared), "token_estimate": 4},
            {"hash": "a3", "file_path": str(extra), "token_estimate": 3},
        ],
    )
    _write_record(chunks_file, query_hash="req-2", chunks=[{"hash": "b1", "token_estimate": 9}])
    _write_record(
        chunks_file,
        query_hash="req-3",
        chunks=[{"hash": "c1", "file_path": str(tmp_path / "missing.py"), "token_estimate": 5}],
    )

    result = aggregate_recall_savings(chunks_file)

    assert result.requests == 1
    assert result.returned_tokens == 13
    assert result.counterfactual_tokens == 15
    assert result.savings_ratio == 1 - (13 / 15)
    assert result.rows_skipped_no_file_path == 1
    assert result.rows_skipped_missing_files == 1
    assert result.missing_file_refs == 1
    assert result.request_rows[0].query_hash == "req-1"


def test_aggregate_recall_savings_uses_most_recent_line_limit(tmp_path: Path) -> None:
    old = tmp_path / "old.py"
    old.write_text("x" * 40, encoding="utf-8")
    recent = tmp_path / "recent.py"
    recent.write_text("y" * 80, encoding="utf-8")
    chunks_file = tmp_path / "chunks.jsonl"

    _write_record(
        chunks_file,
        query_hash="old",
        chunks=[{"hash": "old", "file_path": str(old), "token_estimate": 1}],
    )
    _write_record(
        chunks_file,
        query_hash="recent",
        chunks=[{"hash": "recent", "file_path": str(recent), "token_estimate": 2}],
    )

    result = aggregate_recall_savings(chunks_file, max_lines=1)

    assert result.requests == 1
    assert result.request_rows[0].query_hash == "recent"
    assert result.returned_tokens == 2
    assert result.counterfactual_tokens == 20
