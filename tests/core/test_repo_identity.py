"""Tests for stable git repository identity (dec-129)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from axon.core.repo_identity import repo_identity


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


def test_main_checkout_identity_is_its_directory_name(main_checkout: Path) -> None:
    assert repo_identity(main_checkout) == "myrepo"


def test_worktree_shares_the_main_checkouts_identity(worktree: Path) -> None:
    assert worktree.name == "agent-issue-158"
    assert repo_identity(worktree) == "myrepo"


def test_subdirectory_of_a_worktree_resolves_to_the_same_identity(
    main_checkout: Path, worktree: Path
) -> None:
    main_subdirectory = main_checkout / "src" / "pkg"
    worktree_subdirectory = worktree / "src" / "pkg"
    main_subdirectory.mkdir(parents=True)
    worktree_subdirectory.mkdir(parents=True)

    assert repo_identity(main_subdirectory) == "myrepo"
    assert repo_identity(worktree_subdirectory) == "myrepo"


def test_unrelated_repos_do_not_collide(main_checkout: Path, tmp_path: Path) -> None:
    other = tmp_path / "otherrepo"
    other.mkdir()
    _git(["init"], other)

    assert repo_identity(other) == "otherrepo"
    assert repo_identity(other) != repo_identity(main_checkout)


def test_non_git_directory_falls_back_to_the_basename(tmp_path: Path) -> None:
    plain = tmp_path / "plainrepo"
    plain.mkdir()

    assert repo_identity(plain) == "plainrepo"
