from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _script_path() -> Path:
    return Path(__file__).resolve().parents[2] / "scripts" / "recall_savings_report.py"


def test_report_computes_request_and_aggregate_savings(tmp_path: Path) -> None:
    shared = tmp_path / "shared.py"
    shared.write_text("x" * 40, encoding="utf-8")
    extra = tmp_path / "extra.py"
    extra.write_text("y" * 20, encoding="utf-8")

    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-07-03T00:00:00+00:00",
                        "query_hash": "req-1",
                        "strategy": "balanced",
                        "requested_max_tokens": 2000,
                        "chunks": [
                            {
                                "hash": "a1",
                                "file_path": str(shared),
                                "token_estimate": 6,
                            },
                            {
                                "hash": "a2",
                                "file_path": str(shared),
                                "token_estimate": 4,
                            },
                            {
                                "hash": "a3",
                                "file_path": str(extra),
                                "token_estimate": 3,
                            },
                        ],
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-07-03T00:00:01+00:00",
                        "query_hash": "req-2",
                        "strategy": "balanced",
                        "requested_max_tokens": 2000,
                        "chunks": [{"hash": "b1", "token_estimate": 9}],
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-07-03T00:00:02+00:00",
                        "query_hash": "req-3",
                        "strategy": "balanced",
                        "requested_max_tokens": 2000,
                        "chunks": [
                            {
                                "hash": "c1",
                                "file_path": str(tmp_path / "missing.py"),
                                "token_estimate": 5,
                            }
                        ],
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(_script_path()), "--file", str(chunks)],
        capture_output=True,
        text=True,
        check=True,
    )

    out = result.stdout
    assert (
        "METHOD: counterfactual = reading each source file in full "
        "(Read/grep workflow); telemetry rows without file_path (pre-T8) are excluded"
    ) in out
    assert "request req-1 returned=13 counterfactual=15 savings_ratio=0.1333 missing_files=0" in out
    assert "requests=1" in out
    assert "returned_tokens=13" in out
    assert "counterfactual_tokens=15" in out
    assert "savings_ratio=0.1333" in out
    assert "rows_skipped_no_file_path=1" in out
    assert "rows_skipped_missing_files=1" in out


def test_help_works() -> None:
    result = subprocess.run(
        [sys.executable, str(_script_path()), "--help"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "usage:" in result.stdout.lower()
    assert "chunks.jsonl" in result.stdout
