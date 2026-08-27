"""Guard the CI secret scan's direct, full-history CLI invocation."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
JOB = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))["jobs"][
    "secret-scan"
]


def _scan_step():
    steps = [
        step
        for step in JOB["steps"]
        if re.search(r"gitleaks[\"']?\s+(git|detect)\b", step.get("run", ""))
    ]
    assert len(steps) == 1
    return steps[0]


def _install_step():
    return next(step for step in JOB["steps"] if step.get("name") == "install gitleaks 8.30.1")


def test_secret_scan_runs_the_cli_not_an_action():
    assert all(
        step.get("uses", "actions/checkout@").startswith("actions/checkout@")
        for step in JOB["steps"]
    )
    run = _scan_step()["run"]
    assert "--redact" in run and "--verbose" in run


def test_secret_scan_needs_no_github_token():
    assert JOB["permissions"] == {"contents": "read"}
    assert all(
        "GITHUB_TOKEN" not in env
        for env in [JOB.get("env", {}), *(step.get("env", {}) for step in JOB["steps"])]
    )


def test_secret_scan_covers_full_history():
    checkout = next(step for step in JOB["steps"] if step.get("uses") == "actions/checkout@v4")
    assert checkout["with"]["fetch-depth"] == 0
    run = _scan_step()["run"]
    assert "--log-opts" not in run and "--staged" not in run and "--pre-commit" not in run


def test_secret_scan_cannot_disarm_gitleaks():
    scan = _scan_step()
    assert not JOB.get("continue-on-error", False) and not scan.get("continue-on-error", False)
    assert all(token not in scan["run"] for token in ("|| true", "|| exit 0", "set +e", "|"))


def test_gitleaks_download_is_verified():
    run = _install_step()["run"]
    assert "sha256sum -c" in run
    assert "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb" in run
