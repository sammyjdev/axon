from __future__ import annotations

import json
from pathlib import Path

from axon.doctor import CheckStatus
from axon.doctor.checks.recall_coverage import check_recall_coverage


def _telemetry(tmp_path: Path, *, opportunities: int, searches: int) -> Path:
    recall = tmp_path / "recall"
    recall.mkdir(parents=True, exist_ok=True)

    source = tmp_path / "source.py"
    source.write_text("x" * 400, encoding="utf-8")
    (recall / "chunks.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "ts": "2026-08-07T13:00:00+00:00",
                    "query_hash": f"q{i}",
                    "chunks": [{"file_path": str(source), "token_estimate": 10}],
                }
            )
            + "\n"
            for i in range(searches)
        ),
        encoding="utf-8",
    )
    (recall / "opportunities.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "ts": "2026-08-07T12:00:00+00:00",
                    "session": "s1",
                    "repo": "axon",
                    "path": str(source),
                    "est_tokens_full": 100,
                    "searches_in_session": 0,
                }
            )
            + "\n"
            for _ in range(opportunities)
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_coverage_reports_delivered_savings_not_just_the_per_call_ratio(
    tmp_path: Path,
) -> None:
    _telemetry(tmp_path, opportunities=3, searches=1)

    result = check_recall_coverage(data_root=tmp_path)

    assert result.status is CheckStatus.OK
    # per-call savings is 90%, but the tool was reached for 1 time in 4
    assert "coverage=25.0%" in result.detail
    assert "delivered=22.5%" in result.detail


def test_coverage_warns_when_the_tool_is_barely_reached_for(tmp_path: Path) -> None:
    """A high per-call ratio at near-zero coverage is the exact failure this
    check exists to surface, so it must not report OK."""
    _telemetry(tmp_path, opportunities=99, searches=1)

    result = check_recall_coverage(data_root=tmp_path)

    assert result.status is CheckStatus.WARN
    assert "coverage=1.0%" in result.detail


def test_coverage_skips_cleanly_without_harness_telemetry(tmp_path: Path) -> None:
    result = check_recall_coverage(data_root=tmp_path)

    assert result.status is CheckStatus.OK
    assert result.detail == "skipped: no coverage telemetry yet"
