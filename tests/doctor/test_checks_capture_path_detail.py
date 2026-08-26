"""Detail-format regression tests for the round-2 fixes to issue #141.

Covers three defects an independent reviewer confirmed live on the shipped
binary:

- B1: ``install.freshness`` FAIL detail used to be an unbounded, basename-only
  list (``__init__.py, __init__.py, ...``) that could not name which file was
  actually stale. It must now cap at 5 examples, use relpaths, and signal
  truncation.
- N1: ``capture.gap`` used to claim ``"skipped: no onboarded repos"`` even
  when repos exist but every ``git log`` call failed - a false diagnostic.
- N2: ``check_hook_interpreters`` used to silently drop a repo whose AXON
  block has no parseable interpreter line, then count it toward the healthy
  total. It must now surface the malformed repo by name.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from axon.doctor import CheckStatus
from axon.doctor.checks.capture_path import (
    check_capture_gap,
    check_hook_interpreters,
    check_install_freshness,
)
from axon.hooks.git_installer import _BEGIN, _END


@pytest.fixture(autouse=True)
def _isolate_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AXON_DEV_ROOT", raising=False)
    monkeypatch.setenv("AXON_ENGINE", str(tmp_path / "engine"))
    monkeypatch.setenv("AXON_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), *args],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    )


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


# Seven .py files under src/axon - more than the 5-example cap. Three share
# the basename __init__.py across different packages, so a fix that reverts
# to Path(p).name (rather than a relpath) collapses them into indistinguishable
# duplicates.
_MANY_ENGINE_FILES: dict[str, str] = {
    "src/axon/__init__.py": "# root\n",
    "src/axon/a/__init__.py": "# a\n",
    "src/axon/b/__init__.py": "# b\n",
    "src/axon/c/__init__.py": "# c\n",
    "src/axon/d/mod.py": "# d\n",
    "src/axon/e/mod.py": "# e\n",
    "src/axon/f/mod.py": "# f\n",
}


def _make_many_files_engine_repo(tmp_path: Path) -> Path:
    engine = tmp_path / "engine-repo"
    engine.mkdir()
    _git(engine, "init")
    _git(engine, "config", "user.email", "axon@example.com")
    _git(engine, "config", "user.name", "Axon Test")
    for rel, content in _MANY_ENGINE_FILES.items():
        target = engine / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    _git(engine, "add", "-A")
    _git(engine, "commit", "-m", "engine snapshot")
    return engine


class TestInstallFreshnessDetailTruncation:
    def test_more_than_five_missing_files_truncates_with_distinct_relpaths(
        self, tmp_path: Path
    ) -> None:
        engine = _make_many_files_engine_repo(tmp_path)
        # Nothing installed at all: every tracked file is "missing".
        installed = tmp_path / "installed" / "axon"

        result = check_install_freshness(engine_root=engine, installed_package=installed)

        assert result.status is CheckStatus.FAIL
        assert f"{len(_MANY_ENGINE_FILES)} missing, 0 stale" in result.detail

        # Distinguishes files that share a basename via their relpath - the
        # regression this pins is Path(p).name collapsing these to one entry.
        assert "a/__init__.py" in result.detail
        assert "b/__init__.py" in result.detail

        # Capped at 5 examples: the 6th and 7th (by git ls-tree order) do
        # not appear individually, and truncation is signalled explicitly.
        assert "e/mod.py" not in result.detail
        assert "f/mod.py" not in result.detail
        assert "(+2 more)" in result.detail


class TestCaptureGapHonestSkipDetail:
    def test_onboarded_repo_with_failing_git_log_is_not_reported_as_no_repos(
        self, tmp_path: Path
    ) -> None:
        dev_root = tmp_path / "dev"
        # Has an AXON hook (so it counts as onboarded) but is not an actual
        # git repository - `git log` inside it fails.
        _make_hook_repo(dev_root / "notarealrepo", sys.executable)

        result = check_capture_gap(dev_root=dev_root)

        assert result.status is CheckStatus.OK
        assert result.detail.startswith("skipped: ")
        assert result.detail != "skipped: no onboarded repos"
        assert "1" in result.detail


class TestHookInterpreterMalformedBlock:
    def test_malformed_hook_block_is_named_and_not_counted_healthy(
        self, tmp_path: Path
    ) -> None:
        dev_root = tmp_path / "dev"
        _make_hook_repo(dev_root / "healthyrepo", sys.executable)

        # AXON block present but with no `-m axon.hooks.git_event` line at
        # all, so `_hook_interpreter` returns None for it.
        malformed = dev_root / "malformedrepo"
        hooks = malformed / ".git" / "hooks"
        hooks.mkdir(parents=True)
        (hooks / "post-commit").write_text(
            f"#!/usr/bin/env bash\n{_BEGIN}\necho no interpreter line here\n{_END}\n",
            encoding="utf-8",
        )

        result = check_hook_interpreters(dev_root=dev_root)

        # Status stays OK (nothing is actually broken), but the malformed
        # repo must be named explicitly - not silently folded into a "2
        # onboarded repo(s) healthy" count as if both repos were fine.
        assert result.status is CheckStatus.OK
        assert "malformedrepo" in result.detail
        assert "2 onboarded repo(s)" not in result.detail
        assert "1 onboarded repo(s) healthy" in result.detail
