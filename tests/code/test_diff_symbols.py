"""Tests for commit-diff symbol detection (T4.3)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from axon.code.diff_symbols import symbols_touched_by_commit

_TWO_FUNCS = "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n"


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "r"
    r.mkdir()
    _git(["init"], r)
    _git(["config", "user.email", "t@axon.dev"], r)
    _git(["config", "user.name", "T"], r)
    (r / "mod.py").write_text(_TWO_FUNCS, encoding="utf-8")
    _git(["add", "."], r)
    _git(["commit", "-m", "feat: add mod"], r)
    return r


def test_initial_commit_touches_all_symbols(repo: Path) -> None:
    head = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    touched = {s.id for s in symbols_touched_by_commit(repo, head)}
    assert touched == {"alpha", "beta"}


def test_partial_change_touches_only_changed_symbol(repo: Path) -> None:
    (repo / "mod.py").write_text(
        _TWO_FUNCS.replace("return 2", "return 99"), encoding="utf-8"
    )
    _git(["commit", "-am", "fix: tweak beta"], repo)
    head = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    touched = {s.id for s in symbols_touched_by_commit(repo, head)}
    assert touched == {"beta"}
