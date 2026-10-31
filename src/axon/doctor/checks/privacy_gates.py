"""Warn when a privacy gate is open because its variable is unset.

Both gates fail open: without AXON_RESTRICTED_ROOT the importer guards a
placeholder tree, so sessions from the real restricted tree are imported;
without AXON_WORK_PROJECTS no project needs ctx="work". The checks only warn.
"""

from __future__ import annotations

import os

from axon.config.runtime import work_projects
from axon.doctor import CheckResult, CheckStatus


def check_restricted_root() -> CheckResult:
    if (os.environ.get("AXON_RESTRICTED_ROOT") or "").strip():
        return CheckResult(
            name="privacy.restricted_root",
            status=CheckStatus.OK,
            detail="AXON_RESTRICTED_ROOT is set",
        )
    return CheckResult(
        name="privacy.restricted_root",
        status=CheckStatus.WARN,
        detail="AXON_RESTRICTED_ROOT is unset: sessions from the restricted tree are imported",
        suggestion="set AXON_RESTRICTED_ROOT for the hook and MCP processes (see .env.example)",
    )


def check_work_projects() -> CheckResult:
    if work_projects():
        return CheckResult(
            name="privacy.work_projects",
            status=CheckStatus.OK,
            detail="AXON_WORK_PROJECTS is set",
        )
    return CheckResult(
        name="privacy.work_projects",
        status=CheckStatus.WARN,
        detail=(
            "AXON_WORK_PROJECTS is unset: work ADRs are served without ctx='work' "
            "and `adr sync` files them under personal/ unless an old manifest lists them"
        ),
        suggestion="set AXON_WORK_PROJECTS for the hook and MCP processes (see .env.example)",
    )
