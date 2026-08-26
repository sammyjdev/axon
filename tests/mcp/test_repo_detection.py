"""Tests for MCP repository detection in linked worktrees (dec-129)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(  # noqa: S603
        ["git", *args], cwd=cwd, check=True, capture_output=True  # noqa: S603, S607
    )


@pytest.fixture
def main_checkout(tmp_path: Path) -> Path:
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _git(["init", "-b", "main"], repo)
    _git(["config", "user.email", "test@axon.dev"], repo)
    _git(["config", "user.name", "AXON Test"], repo)
    (repo / "main.py").write_text("print('hello')\n", encoding="utf-8")
    _git(["add", "."], repo)
    _git(["commit", "-m", "feat: add the entry point"], repo)
    return repo


@pytest.fixture
def worktree(main_checkout: Path, tmp_path: Path) -> Path:
    wt = tmp_path / "agent-issue-158"
    _git(["worktree", "add", "-b", "issue-158", str(wt)], main_checkout)
    return wt


def test_detect_repo_uses_the_parent_repo_for_a_worktree(
    worktree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(worktree)

    from axon.mcp import server

    assert server._detect_repo() == "myrepo"


def test_detect_repo_keeps_the_main_checkouts_name(
    main_checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(main_checkout)

    from axon.mcp import server

    assert server._detect_repo() == "myrepo"
