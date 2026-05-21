"""Git worktree manager.

Each task gets an isolated worktree on a ``praxis/<task-id>`` branch. Cleanup
removes the worktree directory, deletes the branch, and prunes git's worktree
administrative state so nothing is left behind.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

BRANCH_PREFIX = "praxis/"


class WorktreeError(RuntimeError):
    """Raised when a git worktree operation fails."""


@dataclass
class Worktree:
    task_id: str
    branch: str
    path: Path


class WorktreeManager:
    """Create and clean up per-task git worktrees."""

    def __init__(
        self,
        repo_root: str | Path | None = None,
        base_dir: str | Path | None = None,
    ) -> None:
        self.repo_root = Path(repo_root or Path.cwd()).resolve()
        if base_dir is not None:
            self.base_dir = Path(base_dir).resolve()
        else:
            self.base_dir = self.repo_root / ".praxis" / "worktrees"

    def _git(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise WorktreeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
        return proc.stdout.strip()

    @staticmethod
    def branch_for(task_id: str) -> str:
        return f"{BRANCH_PREFIX}{task_id}"

    def path_for(self, task_id: str) -> Path:
        return self.base_dir / task_id

    def _branch_exists(self, branch: str) -> bool:
        return bool(self._git("branch", "--list", branch))

    def create(self, task_id: str, ref: str = "HEAD") -> Worktree:
        """Create a worktree for ``task_id`` on a fresh ``praxis/<id>`` branch."""
        branch = self.branch_for(task_id)
        path = self.path_for(task_id)
        if path.exists():
            raise WorktreeError(f"worktree path already exists: {path}")
        if self._branch_exists(branch):
            raise WorktreeError(f"branch already exists: {branch}")
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._git("worktree", "add", "-b", branch, str(path), ref)
        return Worktree(task_id=task_id, branch=branch, path=path)

    def list(self) -> list[Worktree]:
        """Return every registered worktree on a ``praxis/`` branch."""
        out = self._git("worktree", "list", "--porcelain")
        worktrees: list[Worktree] = []
        current: dict[str, str] = {}
        for line in [*out.splitlines(), ""]:
            if not line.strip():
                ref = current.get("branch", "")
                wt_path = current.get("worktree")
                if wt_path and ref.startswith(f"refs/heads/{BRANCH_PREFIX}"):
                    short = ref.removeprefix("refs/heads/")
                    worktrees.append(
                        Worktree(
                            task_id=short.removeprefix(BRANCH_PREFIX),
                            branch=short,
                            path=Path(wt_path),
                        )
                    )
                current = {}
                continue
            key, _, value = line.partition(" ")
            current[key] = value
        return worktrees

    def remove(self, task_id: str) -> None:
        """Remove the worktree and branch for ``task_id``, leaving nothing behind."""
        branch = self.branch_for(task_id)
        path = self.path_for(task_id)
        if path.exists():
            try:
                self._git("worktree", "remove", "--force", str(path))
            except WorktreeError:
                shutil.rmtree(path, ignore_errors=True)
        self._git("worktree", "prune")
        if self._branch_exists(branch):
            self._git("branch", "-D", branch)

    def cleanup_all(self) -> None:
        """Remove every ``praxis/`` worktree this manager knows about."""
        for worktree in self.list():
            self.remove(worktree.task_id)
