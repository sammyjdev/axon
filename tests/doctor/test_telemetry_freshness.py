"""Telemetry that stopped must be a failure, not silence.

Before this check the doctor had exactly one look at any telemetry series -
`recall.savings` - and it returns OK whether the series is healthy, empty or
two months stale. Nothing could fail, so `recall/requests.jsonl` went 38 days
without a single write and the only reason anyone noticed was someone listing
mtimes by hand.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from axon.doctor import CheckStatus
from axon.doctor.checks.telemetry_freshness import SERIES, check_telemetry_freshness


def _write(root: Path, rel: str, when: datetime) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ts": when.isoformat()}) + "\n", encoding="utf-8")


def _all_fresh(root: Path) -> None:
    now = datetime.now(UTC)
    for series in SERIES:
        _write(root, series.path, now)


def test_all_series_recent_is_ok(tmp_path: Path) -> None:
    _all_fresh(tmp_path)

    result = check_telemetry_freshness(data_root=tmp_path)

    assert result.status is CheckStatus.OK
    assert f"{len(SERIES)}/{len(SERIES)}" in result.detail


def test_one_stale_series_fails_and_names_its_writer(tmp_path: Path) -> None:
    """The real 2026-08-29 case: one series dead, its siblings still writing."""
    _all_fresh(tmp_path)
    dead = SERIES[0]
    _write(tmp_path, dead.path, datetime.now(UTC) - dead.max_quiet - timedelta(days=1))

    result = check_telemetry_freshness(data_root=tmp_path)

    assert result.status is CheckStatus.FAIL
    assert dead.path in result.detail
    assert dead.written_by in result.detail, (
        "a failure has to name what writes the series, or the reader starts from zero"
    )


def test_a_series_just_inside_its_window_is_not_flagged(tmp_path: Path) -> None:
    """The boundary matters: an off-by-one here cries wolf on every quiet week."""
    _all_fresh(tmp_path)
    series = SERIES[0]
    _write(tmp_path, series.path, datetime.now(UTC) - series.max_quiet + timedelta(hours=1))

    assert check_telemetry_freshness(data_root=tmp_path).status is CheckStatus.OK


def test_no_telemetry_at_all_warns_rather_than_passing(tmp_path: Path) -> None:
    """A fresh install has nothing to judge - but silence is not health.

    Returning OK here is exactly what makes recall.savings unable to fail.
    """
    result = check_telemetry_freshness(data_root=tmp_path)

    assert result.status is CheckStatus.WARN


def test_an_unparseable_tail_is_not_read_as_fresh(tmp_path: Path) -> None:
    """Garbage in the last lines must not count as a recent event."""
    _all_fresh(tmp_path)
    path = tmp_path / SERIES[0].path
    path.write_text("not json\n{also not\n", encoding="utf-8")

    result = check_telemetry_freshness(data_root=tmp_path)

    assert result.status is not CheckStatus.OK
    assert "no parseable record" in result.detail
