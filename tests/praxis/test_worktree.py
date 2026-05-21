"""Task 11 — git worktree manager creates and fully cleans up worktrees."""

from __future__ import annotations

import subprocess
from pathlib import Path

from praxis.worktree import WorktreeManager


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "praxis@example.com"], cwd=path, check=True
    )
    subprocess.run(["git", "config", "user.name", "Praxis Test"], cwd=path, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=path, check=True)
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=path, check=True)


def _branches(repo: Path) -> str:
    return subprocess.run(
        ["git", "branch", "--list"], cwd=repo, capture_output=True, text=True
    ).stdout


def test_create_and_cleanup_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    manager = WorktreeManager(repo_root=repo)
    worktree = manager.create("alpha-123")

    assert worktree.branch == "praxis/alpha-123"
    assert worktree.path.exists()
    assert (worktree.path / "README.md").exists()
    assert any(w.task_id == "alpha-123" for w in manager.list())
    assert "praxis/alpha-123" in _branches(repo)

    manager.remove("alpha-123")

    assert not worktree.path.exists()
    assert manager.list() == []
    assert "praxis/alpha-123" not in _branches(repo)

    admin = repo / ".git" / "worktrees"
    assert not admin.exists() or not any(admin.iterdir())


def test_cleanup_all_removes_every_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    manager = WorktreeManager(repo_root=repo)
    manager.create("one")
    manager.create("two")
    assert len(manager.list()) == 2

    manager.cleanup_all()

    assert manager.list() == []
    assert "praxis/one" not in _branches(repo)
    assert "praxis/two" not in _branches(repo)
