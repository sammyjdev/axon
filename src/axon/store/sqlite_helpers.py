"""Shared SQLite helpers used by session_store and session_repository (MS-6).

These three helpers were previously duplicated in both modules.  Defining them
once here eliminates the drift risk.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite

from axon.config.data_root import data_root
from axon.store.pending import PendingPaths


def _is_db_locked(exc: Exception) -> bool:
    """Return True if ``exc`` indicates SQLite write contention."""
    if not isinstance(exc, aiosqlite.OperationalError):
        return False
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def _pending_paths() -> PendingPaths:
    """Resolve the pending/quarantine layout under the AXON data root."""
    root = data_root()
    return PendingPaths(
        pending_dir=root / "pending",
        quarantine_dir=root / "pending-quarantine",
        quarantine_log=root / "quarantine.jsonl",
    )


def _warnings_log() -> Path:
    """Return the path to the capture-warnings log."""
    return data_root() / "capture-warnings.jsonl"
