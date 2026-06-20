from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def test_iter_git_files_returns_only_matching_suffixes(tmp_path: Path) -> None:
    import subprocess
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True, capture_output=True)
    (repo / "a.py").write_text("pass\n")
    (repo / "b.ts").write_text("const x = 1;\n")
    (repo / "c.md").write_text("# doc\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)

    from axon.repo.file_walk import iter_git_files
    files = iter_git_files(repo, suffixes={".py"})
    names = {f.name for f in files}
    assert "a.py" in names
    assert "b.ts" not in names
    assert "c.md" not in names
