"""Nested dev-tree layout coverage for `_onboarded_repos` (issue #141 fix round).

The home tree was reorganised into `~/dev/{products,tools,learning}/<repo>` in
2026-07. `_onboarded_repos` used to scan only immediate children of the dev
root, so it missed every repo one level deeper. These tests pin the fixed
two-level glob: depth 1 and depth 2 are both found, depth 3 is documented as
out of scope.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest

from axon.doctor import CheckStatus
from axon.doctor.checks.capture_path import check_hook_interpreters
from axon.hooks.git_installer import _BEGIN, _END

_BROKEN_INTERPRETER = "/nonexistent/python"


@pytest.fixture(autouse=True)
def _isolate_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AXON_DEV_ROOT", raising=False)
    monkeypatch.setenv("AXON_ENGINE", str(tmp_path / "engine"))
    monkeypatch.setenv("AXON_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")


def _hook_text(interpreter: str) -> str:
    return (
        "#!/usr/bin/env bash\n"
        f"{_BEGIN}\n"
        f"{shlex.quote(interpreter)} -m axon.hooks.git_event commit"
        " 2>/dev/null || true\n"
        f"{_END}\n"
    )


def _make_hook_repo(repo: Path, interpreter: str) -> Path:
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "post-commit").write_text(_hook_text(interpreter), encoding="utf-8")
    return repo


class TestNestedLayout:
    def test_finds_both_depth_one_and_depth_two_repos(self, tmp_path: Path) -> None:
        dev_root = tmp_path / "dev"
        _make_hook_repo(dev_root / "flatrepo", sys.executable)
        _make_hook_repo(dev_root / "products" / "nestedrepo", sys.executable)

        result = check_hook_interpreters(dev_root=dev_root)

        assert result.status is CheckStatus.OK
        assert "2 onboarded repo(s)" in result.detail

    def test_broken_nested_repo_fails_and_names_the_nested_repo(
        self, tmp_path: Path
    ) -> None:
        dev_root = tmp_path / "dev"
        _make_hook_repo(dev_root / "flatrepo", sys.executable)
        _make_hook_repo(dev_root / "products" / "nestedrepo", _BROKEN_INTERPRETER)

        result = check_hook_interpreters(dev_root=dev_root)

        assert result.status is CheckStatus.FAIL
        assert "nestedrepo" in result.detail
        assert "flatrepo" not in result.detail

    def test_depth_three_repo_is_not_found(self, tmp_path: Path) -> None:
        """Pins the two-level scan ceiling only.

        The repo below has a HEALTHY interpreter - if it were found, the
        check would report OK for an unrelated reason (nothing broken to
        report). Using a healthy interpreter here isolates the assertion to
        scan depth alone: a repo nested deeper than two levels is out of
        scope for this check, whether its hook is healthy or broken.
        """
        dev_root = tmp_path / "dev"
        _make_hook_repo(dev_root / "a" / "b" / "toodeeprepo", sys.executable)

        result = check_hook_interpreters(dev_root=dev_root)

        assert result.status is CheckStatus.OK
        assert result.detail == "skipped: no onboarded repos"
