"""Activity spool operations wrapping pending.py."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from axon.config.data_root import data_root
from axon.store.pending import (
    DrainResult,
    PendingPaths,
    drain_pending,
    write_pending,
)


def activity_spool_paths() -> PendingPaths:
    """Activity-specific spool dirs under the AXON data root, isolated from the
    session pending/quarantine dirs.
    """
    root = data_root()
    return PendingPaths(
        pending_dir=root / "activity-pending",
        quarantine_dir=root / "activity-pending-quarantine",
        quarantine_log=root / "activity-quarantine.jsonl",
    )


async def spool_event(event: dict, *, paths: PendingPaths | None = None) -> Path:
    """Atomically spool one sanitized event dict. Wraps write_pending with
    commit_hash=event["event_id"] (spool identity is the deterministic event_id,
    not a git commit).
    """
    if paths is None:
        paths = activity_spool_paths()
    return await write_pending(
        payload=event,
        commit_hash=event["event_id"],
        paths=paths,
    )


async def drain_spool(
    paths: PendingPaths | None,
    *,
    sink: Callable[[dict], Awaitable[None]],
    is_retryable: Callable[[Exception], bool] = lambda _e: False,
) -> DrainResult:
    """Thin wrapper over drain_pending using the activity spool paths."""
    if paths is None:
        paths = activity_spool_paths()
    return await drain_pending(
        paths=paths,
        sink=sink,
        is_retryable=is_retryable,
    )
