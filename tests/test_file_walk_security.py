from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Minimal git repo with identity configured."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@test.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True, capture_output=True)
    return repo


def test_gitignored_files_never_embedded(git_repo: Path) -> None:
    """Gitignored files are not returned even if their suffix matches."""
    (git_repo / ".gitignore").write_text(".env\nsecrets.json\n")
    (git_repo / ".env").write_text("SECRET_KEY=abc123\n")
    (git_repo / "secrets.json").write_text('{"password": "hunter2"}\n')
    (git_repo / "main.py").write_text("def hello(): pass\n")
    subprocess.run(["git", "-C", str(git_repo), "add", ".gitignore", "main.py"], check=True, capture_output=True)

    from axon.repo.file_walk import iter_git_files
    files = iter_git_files(git_repo, suffixes={".py", ".env", ".json"})

    names = {f.name for f in files}
    assert ".env" not in names, ".env must not be returned (gitignored)"
    assert "secrets.json" not in names, "secrets.json must not be returned (gitignored)"
    assert "main.py" in names, "main.py must be returned (tracked, not gitignored)"


def test_committed_then_gitignored_never_embedded(git_repo: Path) -> None:
    """A real-suffix file (.py) committed BEFORE a matching .gitignore rule must
    still be excluded. It stays in 'git ls-files --cached' (tracked), so only a
    'check-ignore --no-index' pass can catch it. Placed in a subdirectory so the
    relative path exercises path-separator handling on Windows."""
    # First commit: src/config.py (tracked .py) with no gitignore yet.
    (git_repo / "src").mkdir()
    (git_repo / "src" / "config.py").write_text("API_KEY = 'leak'\n")
    (git_repo / "main.py").write_text("def hello(): pass\n")
    subprocess.run(["git", "-C", str(git_repo), "add", "src/config.py", "main.py"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(git_repo), "commit", "-m", "initial"], check=True, capture_output=True)

    # Second commit: add .gitignore that excludes the already-committed config.py.
    (git_repo / ".gitignore").write_text("src/config.py\n")
    subprocess.run(["git", "-C", str(git_repo), "add", ".gitignore"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(git_repo), "commit", "-m", "gitignore"], check=True, capture_output=True)

    from axon.repo.file_walk import iter_git_files
    files = iter_git_files(git_repo, suffixes={".py"})

    names = {f.name for f in files}
    assert "config.py" not in names, "config.py is tracked but gitignored post-commit; must be excluded"
    assert "main.py" in names


def test_untracked_files_not_returned(git_repo: Path) -> None:
    """Untracked files (never git added) must not be returned."""
    (git_repo / "main.py").write_text("def hello(): pass\n")
    subprocess.run(["git", "-C", str(git_repo), "add", "main.py"], check=True, capture_output=True)
    (git_repo / "untracked.py").write_text("def secret(): pass\n")
    # Do NOT add untracked.py

    from axon.repo.file_walk import iter_git_files
    files = iter_git_files(git_repo, suffixes={".py"})

    names = {f.name for f in files}
    assert "main.py" in names
    assert "untracked.py" not in names


def test_iter_git_files_fallback_outside_git_repo(tmp_path: Path) -> None:
    """Non-git directory falls back to rglob; no crash, returns .py files."""
    (tmp_path / "hello.py").write_text("def hi(): pass\n")
    (tmp_path / "notes.md").write_text("# notes\n")

    from axon.repo.file_walk import iter_git_files
    files = iter_git_files(tmp_path, suffixes={".py"})

    names = {f.name for f in files}
    assert "hello.py" in names
    assert "notes.md" not in names
