"""Tests for the AXON git hook installer (T3.1)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from axon.exceptions import GitAnchorError
from axon.hooks.git_installer import (
    HOOKS,
    hooks_installed,
    install_hooks,
    uninstall_hooks,
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    return tmp_path


def test_install_creates_executable_hooks(repo: Path) -> None:
    installed = install_hooks(repo)
    assert set(installed) == set(HOOKS)
    for hook_name, event in HOOKS.items():
        target = repo / ".git" / "hooks" / hook_name
        assert target.exists()
        assert f"git_event {event}" in target.read_text()
        # Windows has no POSIX executable bit; git runs the hook via the shell
        # regardless, so only assert the bit where it is meaningful.
        if os.name != "nt":
            assert os.stat(target).st_mode & stat.S_IEXEC


def test_install_is_idempotent(repo: Path) -> None:
    install_hooks(repo)
    assert install_hooks(repo) == []  # second run installs nothing
    assert hooks_installed(repo)


def test_uninstall_removes_axon_hooks(repo: Path) -> None:
    install_hooks(repo)
    removed = uninstall_hooks(repo)
    assert set(removed) == set(HOOKS)
    assert not hooks_installed(repo)
    for hook_name in HOOKS:
        assert not (repo / ".git" / "hooks" / hook_name).exists()


def test_install_appends_to_foreign_hook_and_uninstall_preserves_it(repo: Path) -> None:
    foreign = repo / ".git" / "hooks" / "post-commit"
    foreign.write_text("#!/usr/bin/env bash\necho 'pre-existing hook'\n", encoding="utf-8")

    install_hooks(repo)
    after_install = foreign.read_text()
    assert "pre-existing hook" in after_install
    assert "git_event commit" in after_install

    uninstall_hooks(repo)
    after_uninstall = foreign.read_text()
    assert "pre-existing hook" in after_uninstall  # foreign content survives
    assert "git_event" not in after_uninstall


def test_install_outside_git_repo_raises(tmp_path: Path) -> None:
    with pytest.raises(GitAnchorError):
        install_hooks(tmp_path)


def test_hooks_installed_false_when_absent(repo: Path) -> None:
    assert hooks_installed(repo) is False
