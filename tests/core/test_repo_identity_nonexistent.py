# tests/core/test_repo_identity_nonexistent.py
"""Tests pinning repo_identity behavior for non-existent names and paths.

Pins the fix:
`repo_identity` on a name or path that does not exist on disk must return
that name (falling back to basename), NEVER resolving through root.parent to
the surrounding repository the process happens to be running in.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from axon.core.repo_identity import repo_identity


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(  # noqa: S603
        ["git", *args], cwd=cwd, check=True, capture_output=True  # noqa: S603, S607
    )


def test_repo_identity_nonexistent_bare_name_returns_name() -> None:
    """A bare name that does not exist on disk returns that name, not the surrounding repo."""
    assert repo_identity("some-other-repo") == "some-other-repo"
    assert repo_identity("totally-unrelated-project") == "totally-unrelated-project"


def test_repo_identity_nonexistent_path_returns_basename() -> None:
    """Non-existent relative or absolute paths fall back to the basename."""
    assert repo_identity(Path("nonexistent-dir/some-repo")) == "some-repo"
    assert repo_identity(Path("/nonexistent-path-for-test/fake-repo")) == "fake-repo"


def test_repo_identity_existing_file_resolves_through_parent(tmp_path: Path) -> None:
    """A genuinely existing file resolves through its parent directory to git."""
    repo = tmp_path / "pinned_repo"
    repo.mkdir()
    _git(["init", "-b", "main"], cwd=repo)
    _git(["config", "user.email", "test@axon.dev"], cwd=repo)
    _git(["config", "user.name", "Test"], cwd=repo)

    code_file = repo / "pkg" / "module.py"
    code_file.parent.mkdir(parents=True)
    code_file.write_text("print('hello')\n", encoding="utf-8")
    _git(["add", "."], cwd=repo)
    _git(["commit", "-m", "init"], cwd=repo)

    assert repo_identity(code_file) == "pinned_repo"
