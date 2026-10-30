"""Doctor — diagnostic checks for AXON's capture and integration layers (dec-114).

The public API is intentionally small:

- ``CheckStatus``: OK / WARN / FAIL
- ``CheckResult``: ``(name, status, detail, suggestion)``
- ``run_all_checks(...)``: returns ``list[CheckResult]``

Existing ``axon doctor`` keeps its current behaviour; this module adds
the dec-114 capture/adr/toolchain checks alongside.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class CheckStatus(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: CheckStatus
    detail: str = ""
    suggestion: str = ""


def run_all_checks(*, data_root: Path | None = None) -> list[CheckResult]:
    """Run the dec-114 check pack. Stable function used by formatters and CI mode."""
    from axon.doctor.checks.capture import (
        check_pending_backlog,
        check_quarantine_size,
        check_warnings_log,
    )
    from axon.doctor.checks.capture_path import (
        check_hook_interpreters,
        check_install_freshness,
    )
    from axon.doctor.checks.install_branch import check_install_branch
    from axon.doctor.checks.telemetry_freshness import check_telemetry_freshness
    from axon.doctor.checks.toolchain import check_commitlint_compat

    return [
        check_pending_backlog(data_root=data_root),
        check_quarantine_size(data_root=data_root),
        check_warnings_log(data_root=data_root),
        check_telemetry_freshness(data_root=data_root),
        check_install_branch(),
        check_commitlint_compat(),
        check_hook_interpreters(),
        check_install_freshness(),
    ]


def run_privacy_checks() -> list[CheckResult]:
    """Warn when a privacy gate's variable is unset. Advisory: kept out of
    ``run_all_checks`` so an unset variable never changes the exit code."""
    from axon.doctor.checks.privacy_gates import check_restricted_root, check_work_projects

    return [check_restricted_root(), check_work_projects()]


def max_severity(results: list[CheckResult]) -> CheckStatus:
    """Return the worst status across results, ordered OK < WARN < FAIL."""
    order = {CheckStatus.OK: 0, CheckStatus.WARN: 1, CheckStatus.FAIL: 2}
    if not results:
        return CheckStatus.OK
    return max(results, key=lambda r: order[r.status]).status


__all__ = [
    "CheckResult",
    "CheckStatus",
    "max_severity",
    "run_all_checks",
    "run_privacy_checks",
]
