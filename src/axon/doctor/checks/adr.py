"""ADR-layer checks for dec-114."""

from __future__ import annotations

from pathlib import Path

from axon.adr.draft_pool import find_stale
from axon.doctor import CheckResult, CheckStatus

_STALE_WARN_COUNT = 1
_STALE_FAIL_COUNT = 10


def check_stale_pending(*, data_root: Path | None = None) -> CheckResult:
    """Warn when drafts have not been revalidated by L1-full within TTL."""
    draft_dir = None
    if data_root is not None:
        draft_dir = data_root / "adr-draft"
    stale = find_stale(draft_dir=draft_dir)
    if not stale:
        return CheckResult(
            name="adr.stale_pending",
            status=CheckStatus.OK,
            detail="no stale drafts",
        )
    count = len(stale)
    if count >= _STALE_FAIL_COUNT:
        return CheckResult(
            name="adr.stale_pending",
            status=CheckStatus.FAIL,
            detail=f"{count} drafts stale (no L1-full > TTL)",
            suggestion="Run `pb adr validate-drafts`.",
        )
    return CheckResult(
        name="adr.stale_pending",
        status=CheckStatus.WARN,
        detail=f"{count} stale draft(s)",
        suggestion="Run `pb adr validate-drafts` to refresh L1-full status.",
    )
