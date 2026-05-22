"""Tests for scope-end detection (T5.2)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from axon.triggers.scope_detector import detect_scope_end, tag_at_head


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "r"
    r.mkdir()
    _git(["init"], r)
    _git(["config", "user.email", "t@axon.dev"], r)
    _git(["config", "user.name", "T"], r)
    (r / "f.py").write_text("x = 1\n", encoding="utf-8")
    _git(["add", "."], r)
    _git(["commit", "-m", "init"], r)
    return r


def test_milestone_signal_wins() -> None:
    signal = detect_scope_end(milestone=True)
    assert signal is not None and signal.reason == "milestone"


def test_git_tag_signal(repo: Path) -> None:
    _git(["tag", "v1.0"], repo)
    signal = detect_scope_end(repo)
    assert signal is not None
    assert signal.reason == "git-tag"
    assert signal.detail == "v1.0"


def test_decision_threshold_signal() -> None:
    signal = detect_scope_end(decisions_since_export=12, threshold=10)
    assert signal is not None and signal.reason == "decision-threshold"


def test_no_signal_when_scope_open(repo: Path) -> None:
    assert detect_scope_end(repo, decisions_since_export=3, threshold=10) is None


def test_tag_at_head_none_without_tag(repo: Path) -> None:
    assert tag_at_head(repo) is None
