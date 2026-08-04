"""Tests that shell out to git must not inherit the developer's global config.

Temp repos created by tests live outside this repo, so they pick up the global
`commit.gpgsign`. With signing on and the key unavailable (hardware token not
plugged in, no TTY for the passphrase), every `git commit` in the suite dies
with "gpg failed to sign the data" — 29 failures traced to exactly this. See
issue #115.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )


def test_temp_repo_does_not_inherit_global_signing_config(tmp_path: Path) -> None:
    _git(["init", "-q"], tmp_path)
    effective = _git(["config", "--get", "commit.gpgsign"], tmp_path).stdout.strip()
    assert effective in ("", "false"), (
        f"temp repo inherited commit.gpgsign={effective!r} from the ambient git "
        "config; tests that commit will fail whenever the signing key is absent"
    )


def test_commit_in_temp_repo_succeeds(tmp_path: Path) -> None:
    """The behaviour the config isolation exists to protect."""
    _git(["init", "-q"], tmp_path)
    _git(["config", "user.email", "test@axon.dev"], tmp_path)
    _git(["config", "user.name", "AXON Test"], tmp_path)
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    _git(["add", "f.txt"], tmp_path)

    result = _git(["commit", "-q", "-m", "initial"], tmp_path)

    assert result.returncode == 0, f"commit failed: {result.stderr}"
