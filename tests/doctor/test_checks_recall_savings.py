from __future__ import annotations

import json
from pathlib import Path

from axon.doctor import CheckStatus
from axon.doctor.checks.recall_savings import check_recall_savings


def test_recall_savings_ok_with_healthy_telemetry(tmp_path: Path) -> None:
    source = tmp_path / "source.py"
    source.write_text("x" * 400, encoding="utf-8")
    chunks_file = tmp_path / "recall" / "chunks.jsonl"
    chunks_file.parent.mkdir()
    chunks_file.write_text(
        json.dumps(
            {
                "query_hash": "req",
                "chunks": [{"file_path": str(source), "token_estimate": 10}],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = check_recall_savings(data_root=tmp_path)

    assert result.status is CheckStatus.OK
    assert result.detail == (
        "savings=90.0% requests=1 returned=10 counterfactual=100 "
        "(vs reading files in full)"
    )


def test_recall_savings_missing_file_is_skipped_ok(tmp_path: Path) -> None:
    result = check_recall_savings(data_root=tmp_path)

    assert result.status is CheckStatus.OK
    assert result.detail == "skipped: no telemetry yet"


def test_recall_savings_rows_without_file_path_are_skipped_ok(tmp_path: Path) -> None:
    chunks_file = tmp_path / "recall" / "chunks.jsonl"
    chunks_file.parent.mkdir()
    chunks_file.write_text(
        json.dumps({"query_hash": "req", "chunks": [{"token_estimate": 10}]}) + "\n",
        encoding="utf-8",
    )

    result = check_recall_savings(data_root=tmp_path)

    assert result.status is CheckStatus.OK
    assert result.detail == "skipped: no telemetry yet"
