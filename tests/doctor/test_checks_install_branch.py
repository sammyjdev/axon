from __future__ import annotations

import subprocess
from pathlib import Path

from axon.doctor import CheckStatus
from axon.doctor.checks import install_branch


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    return root


def test_install_branch_master_is_ok(tmp_path: Path, monkeypatch) -> None:
    root = _repo(tmp_path)
    monkeypatch.setattr(install_branch, "_installed_repo_root", lambda: root)
    monkeypatch.setattr(
        install_branch.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "master\n", ""),
    )

    result = install_branch.check_install_branch()

    assert result.status is CheckStatus.OK
    assert result.detail == "editable install serving branch 'master'"


def test_install_branch_feature_branch_warns(tmp_path: Path, monkeypatch) -> None:
    root = _repo(tmp_path)
    monkeypatch.setattr(install_branch, "_installed_repo_root", lambda: root)
    monkeypatch.setattr(
        install_branch.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "feat/x\n", ""),
    )

    result = install_branch.check_install_branch()

    assert result.status is CheckStatus.WARN
    assert result.detail == (
        "editable install serving branch 'feat/x' (expected master) - "
        "Claude Code MCP runs this checkout"
    )


def test_install_branch_errors_are_skipped(tmp_path: Path, monkeypatch) -> None:
    root = _repo(tmp_path)
    monkeypatch.setattr(install_branch, "_installed_repo_root", lambda: root)

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=2)

    monkeypatch.setattr(install_branch.subprocess, "run", raise_timeout)

    result = install_branch.check_install_branch()

    assert result.status is CheckStatus.OK
    assert result.detail.startswith("skipped:")
