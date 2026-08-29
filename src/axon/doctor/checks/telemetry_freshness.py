"""Are the telemetry series still being written?

Six JSONL series record what this system does, and until now exactly one check
looked at any of them - `recall.savings`, which reports a number and returns OK
whether the series is healthy, empty, or two months stale. Nothing could fail.

What that cost, found by hand on 2026-08-29:

* `data/recall/requests.jsonl` stopped on 2026-07-22 and nobody knew for five
  weeks. Its sibling `chunks.jsonl`, written by the same module, kept going -
  so this was a real failure, not an idle system.
* `data/compression/stats.jsonl` spent August accumulating 117 identical
  26-token events, drowning the signal, while the published p50 of 85.5%
  described a June window that had already closed.

The cadences below are declared, not inferred. Inferring "normal" from the
history of a series that is already broken teaches the check to accept the
breakage; and a series nobody can state an expectation for is a series nobody
is really watching.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from axon.config.runtime import load_runtime_config
from axon.doctor import CheckResult, CheckStatus


@dataclass(frozen=True)
class Series:
    """A telemetry file and how long it may go quiet before that means something."""

    path: str
    max_quiet: timedelta
    #: What writes it, so a failure names the thing to go and look at.
    written_by: str


SERIES: tuple[Series, ...] = (
    Series(
        "recall/requests.jsonl",
        timedelta(days=14),
        "axon.observability.recall_telemetry",
    ),
    Series(
        "recall/chunks.jsonl",
        timedelta(days=14),
        "axon.observability.recall_telemetry",
    ),
    Series(
        "compression/stats.jsonl",
        timedelta(days=14),
        "axon.mcp.server (_COMPRESSION_TELEMETRY)",
    ),
    Series(
        "trace/records.jsonl",
        timedelta(days=14),
        "axon.observability.trace",
    ),
)

_TS_KEYS = ("ts", "timestamp", "created_at")


def _last_event(path: Path) -> datetime | None:
    """Timestamp of the newest parseable record, or None.

    Reads the tail rather than the whole file: these grow to thousands of lines
    and the check runs on every `axon doctor`.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    for line in reversed(lines[-200:]):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        for key in _TS_KEYS:
            raw = record.get(key)
            if not isinstance(raw, str):
                continue
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def check_telemetry_freshness(*, data_root: Path | None = None) -> CheckResult:
    root = data_root or load_runtime_config().data_root
    now = datetime.now(UTC)

    stale: list[str] = []
    missing: list[str] = []
    # A file that exists but yields nothing readable is NOT the same as one that
    # was never created. Folding the two together let a corrupted series pass as
    # OK whenever a sibling was healthy - the check's own version of the bug it
    # exists to catch.
    unreadable: list[str] = []
    fresh = 0

    for series in SERIES:
        path = root / series.path
        if not path.exists():
            missing.append(series.path)
            continue
        last = _last_event(path)
        if last is None:
            unreadable.append(f"{series.path} (no parseable record)")
            continue
        quiet = now - last
        if quiet > series.max_quiet:
            stale.append(f"{series.path} silent {quiet.days}d (writer: {series.written_by})")
        else:
            fresh += 1

    if stale or unreadable:
        return CheckResult(
            name="telemetry.freshness",
            status=CheckStatus.FAIL,
            detail="; ".join(
                ([f"{len(stale)} series stale: " + "; ".join(stale)] if stale else [])
                + (
                    [f"{len(unreadable)} unreadable: " + "; ".join(unreadable)]
                    if unreadable
                    else []
                )
            ),
            suggestion=(
                "A series that stopped is not the same as a system that is idle - "
                "check whether its writer still runs, and whether its siblings kept going."
            ),
        )

    if missing and fresh == 0:
        # Nothing to judge. Not a pass, but not a failure either: a fresh install
        # has no telemetry yet, and calling that OK is how recall.savings ended
        # up unable to fail.
        return CheckResult(
            name="telemetry.freshness",
            status=CheckStatus.WARN,
            detail="no telemetry series present: " + ", ".join(missing),
        )

    detail = f"{fresh}/{len(SERIES)} series fresh"
    if missing:
        detail += f" ({len(missing)} absent: {', '.join(missing)})"
    return CheckResult(name="telemetry.freshness", status=CheckStatus.OK, detail=detail)
