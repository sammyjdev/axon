from __future__ import annotations

import subprocess
from pathlib import Path

import axon
from axon.doctor import CheckResult, CheckStatus

# pipx serves the editable checkout, so Claude Code MCP follows this branch.
_EXPECTED_BRANCH = "master"
_TIMEOUT_S = 2


def _installed_repo_root() -> Path:
    package_file = getattr(axon, "__file__", None)
    if not package_file:
        raise RuntimeError("axon package path unavailable")
    package_path = Path(package_file).resolve()
    if len(package_path.parents) < 3:
        raise RuntimeError("axon package path is too shallow")
    root = package_path.parents[2]
    if not (root / ".git").exists():
        raise RuntimeError("installed package is not inside a git checkout")
    return root


def check_install_branch() -> CheckResult:
    try:
        root = _installed_repo_root()
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError("git branch lookup failed")
        branch = completed.stdout.strip()
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return CheckResult(
            name="install.branch",
            status=CheckStatus.OK,
            detail="skipped: branch unavailable",
        )

    if branch == _EXPECTED_BRANCH:
        return CheckResult(
            name="install.branch",
            status=CheckStatus.OK,
            detail=f"editable install serving branch '{branch}'",
        )
    return CheckResult(
        name="install.branch",
        status=CheckStatus.WARN,
        detail=(
            f"editable install serving branch '{branch}' (expected {_EXPECTED_BRANCH}) - "
            "Claude Code MCP runs this checkout"
        ),
    )
