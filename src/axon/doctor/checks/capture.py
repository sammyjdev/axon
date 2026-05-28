"""Capture-layer checks for dec-114.

- ``check_pending_backlog``: warn when ``.axon/pending/`` accumulates
- ``check_quarantine_size``: warn when ``.axon/pending-quarantine/`` grows
- ``check_warnings_log``: warn when ``capture-warnings.jsonl`` shows
  recent persistent contention
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from axon.config.data_root import data_root
from axon.doctor import CheckResult, CheckStatus

_BACKLOG_WARN_COUNT = 50
_BACKLOG_FAIL_COUNT = 500
_BACKLOG_WARN_AGE_HOURS = 6
_QUARANTINE_WARN_COUNT = 5
_WARNINGS_RECENT_HOURS = 1
_WARNINGS_WARN_COUNT = 10


def _root(data_root_override: Path | None) -> Path:
    if data_root_override is not None:
        return data_root_override
    return data_root()


def check_pending_backlog(*, data_root: Path | None = None) -> CheckResult:
    pending = _root(data_root) / "pending"
    if not pending.exists():
        return CheckResult(
            name="capture.pending_backlog",
            status=CheckStatus.OK,
            detail="no pending dir",
        )
    files = list(pending.glob("*.json"))
    count = len(files)
    if count == 0:
        return CheckResult(
            name="capture.pending_backlog",
            status=CheckStatus.OK,
            detail="empty",
        )

    oldest_age = timedelta()
    if files:
        oldest_mtime = min(f.stat().st_mtime for f in files)
        oldest_age = datetime.now(UTC) - datetime.fromtimestamp(oldest_mtime, tz=UTC)

    if count >= _BACKLOG_FAIL_COUNT:
        return CheckResult(
            name="capture.pending_backlog",
            status=CheckStatus.FAIL,
            detail=f"{count} files in pending/",
            suggestion="Run `pb pending drain`. Investigate persistent SQLite contention.",
        )
    if (
        count >= _BACKLOG_WARN_COUNT
        or oldest_age >= timedelta(hours=_BACKLOG_WARN_AGE_HOURS)
    ):
        return CheckResult(
            name="capture.pending_backlog",
            status=CheckStatus.WARN,
            detail=f"{count} files in pending/, oldest {oldest_age.total_seconds()/3600:.1f}h",
            suggestion="Run `pb pending drain` to flush.",
        )
    return CheckResult(
        name="capture.pending_backlog",
        status=CheckStatus.OK,
        detail=f"{count} files in pending/",
    )


def check_quarantine_size(*, data_root: Path | None = None) -> CheckResult:
    q_dir = _root(data_root) / "pending-quarantine"
    if not q_dir.exists():
        return CheckResult(
            name="capture.quarantine_size",
            status=CheckStatus.OK,
            detail="empty",
        )
    count = sum(1 for _ in q_dir.iterdir())
    if count == 0:
        return CheckResult(
            name="capture.quarantine_size",
            status=CheckStatus.OK,
            detail="empty",
        )
    status = (
        CheckStatus.WARN if count >= _QUARANTINE_WARN_COUNT else CheckStatus.OK
    )
    return CheckResult(
        name="capture.quarantine_size",
        status=status,
        detail=f"{count} quarantined file(s)",
        suggestion=(
            "Inspect `.axon/quarantine.jsonl`; `pb pending recover` to retry."
        ) if status is CheckStatus.WARN else "",
    )


def check_warnings_log(*, data_root: Path | None = None) -> CheckResult:
    log = _root(data_root) / "capture-warnings.jsonl"
    if not log.exists():
        return CheckResult(
            name="capture.warnings_log",
            status=CheckStatus.OK,
            detail="no warnings",
        )
    cutoff = datetime.now(UTC) - timedelta(hours=_WARNINGS_RECENT_HOURS)
    recent = 0
    import json as _json
    for line in log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = _json.loads(line)
            ts = datetime.fromisoformat(entry.get("ts", ""))
        except (ValueError, KeyError):
            continue
        if ts >= cutoff:
            recent += 1
    if recent >= _WARNINGS_WARN_COUNT:
        return CheckResult(
            name="capture.warnings_log",
            status=CheckStatus.WARN,
            detail=f"{recent} warnings in last {_WARNINGS_RECENT_HOURS}h",
            suggestion="Investigate SQLite contention; check pending/ backlog.",
        )
    return CheckResult(
        name="capture.warnings_log",
        status=CheckStatus.OK,
        detail=f"{recent} recent warnings",
    )
