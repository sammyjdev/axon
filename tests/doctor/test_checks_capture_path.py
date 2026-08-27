"""Unit tests for axon.doctor.checks.capture_path (issue #141).

The doctor grows three checks that verify the CAPTURE PATH rather than the
datastores: hook interpreters that can import the hook module, an installed
snapshot that matches HEAD of the engine checkout, and recent commits that
actually produced decisions. Each "Done when" clause is broken on purpose by
a fixture below.

Seam contract these tests rely on (deliberate, mirrors the other check
modules): ``capture_path`` imports ``SessionStore`` and ``subprocess`` at
MODULE level, so the tests replace them through module attributes and no
real Postgres is ever contacted. The AXON hook block is always built with
the installer's ``_BEGIN``/``_END`` markers, never re-spelled.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from axon.doctor import CheckStatus, run_all_checks
from axon.doctor.checks import capture_path
from axon.doctor.checks.capture_path import (
    check_capture_gap,
    check_hook_interpreters,
    check_install_freshness,
)
from axon.hooks.git_installer import _BEGIN, _END

_BROKEN_INTERPRETER = "/nonexistent/python"
_SKIP_NO_REPOS = "skipped: no onboarded repos"
_ENGINE_FILES: dict[str, str] = {
    "src/axon/__init__.py": "# engine package\n",
    "src/axon/doctor/checks/capture.py": "NAME = \"capture\"\n",
    "src/axon/hooks/git_hook.py": "MARKER = \"axon\"\n",
}


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


def _head(repo: Path) -> str:
    completed = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), "rev-parse", "HEAD"],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _hook_text(interpreter: str) -> str:
    return (
        "#!/usr/bin/env bash\n"
        f"{_BEGIN}\n"
        f"{shlex.quote(interpreter)} -m axon.hooks.git_event commit"
        " 2>/dev/null || true\n"
        f"{_END}\n"
    )


def _make_hook_repo(dev_root: Path, name: str, interpreter: str) -> Path:
    repo = dev_root / name
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "post-commit").write_text(_hook_text(interpreter), encoding="utf-8")
    return repo


def _make_capture_repo(dev_root: Path, name: str, commits: int) -> list[str]:
    """A real git repo carrying an AXON post-commit hook.

    Returns the commit hashes, newest last. ``commits=0`` leaves the repo
    without a single commit (``git log`` then exits non-zero).
    """
    repo = dev_root / name
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "axon@example.com")
    _git(repo, "config", "user.name", "Axon Test")
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "post-commit").write_text(_hook_text(sys.executable), encoding="utf-8")
    hashes: list[str] = []
    for i in range(commits):
        (repo / f"f{i}.txt").write_text(f"{repo} content {i}\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", f"commit {i}")
        hashes.append(_head(repo))
    return hashes


def _make_engine_repo(tmp_path: Path) -> Path:
    engine = tmp_path / "engine-repo"
    engine.mkdir()
    _git(engine, "init")
    _git(engine, "config", "user.email", "axon@example.com")
    _git(engine, "config", "user.name", "Axon Test")
    for rel, content in _ENGINE_FILES.items():
        target = engine / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    _git(engine, "add", "-A")
    _git(engine, "commit", "-m", "engine snapshot")
    return engine


def _installed_copy(
    tmp_path: Path, *, skip: str | None = None, mutate: str | None = None
) -> Path:
    installed = tmp_path / "installed" / "axon"
    for rel, content in _ENGINE_FILES.items():
        if rel == skip:
            continue
        target = installed / rel.removeprefix("src/axon/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("MUTATED\n" if rel == mutate else content)
    return installed


def _install_fake_store(
    monkeypatch: pytest.MonkeyPatch,
    decisions_by_repo: dict[str, list[str | None]] | None = None,
    error: Exception | None = None,
) -> None:
    decisions = decisions_by_repo or {}

    class _FakeSessionStore:
        def __init__(self, db_path: object) -> None:
            self.db_path = db_path

        async def init(self) -> None:
            return None

        async def close(self) -> None:
            return None

        async def find_decisions_by_repo(
            self, repo: str, limit: int = 20
        ) -> list[object]:
            if error is not None:
                raise error
            return [SimpleNamespace(git_hash=h) for h in decisions.get(repo, [])]

        async def find_decision_by_git_hash(
            self, git_hash: str, *, repo: str | None = None
        ) -> object | None:
            if error is not None:
                raise error
            pools = [decisions.get(repo, [])] if repo is not None else decisions.values()
            for hashes in pools:
                if git_hash in hashes:
                    return SimpleNamespace(git_hash=git_hash)
            return None

    monkeypatch.setattr(capture_path, "SessionStore", _FakeSessionStore)


class TestHookInterpreters:
    def test_missing_interpreter_fails_and_names_the_repo(
        self, tmp_path: Path
    ) -> None:
        dev_root = tmp_path / "dev"
        _make_hook_repo(dev_root, "brokenrepo", _BROKEN_INTERPRETER)

        result = check_hook_interpreters(dev_root=dev_root)

        assert result.name == "capture.hook_interpreter"
        assert result.status is CheckStatus.FAIL
        assert "brokenrepo" in result.detail
        assert _BROKEN_INTERPRETER in result.detail
        assert "install-hooks" in result.suggestion

    def test_working_interpreter_returns_ok(self, tmp_path: Path) -> None:
        dev_root = tmp_path / "dev"
        _make_hook_repo(dev_root, "healthyrepo", sys.executable)

        result = check_hook_interpreters(dev_root=dev_root)

        assert result.status is CheckStatus.OK
        assert result.detail != _SKIP_NO_REPOS

    def test_interpreter_with_nonzero_exit_is_broken(self, tmp_path: Path) -> None:
        script = tmp_path / "fake-python"
        script.write_text("#!/bin/sh\nexit 1\n")
        script.chmod(0o755)
        dev_root = tmp_path / "dev"
        _make_hook_repo(dev_root, "rcfailrepo", str(script))

        result = check_hook_interpreters(dev_root=dev_root)

        assert result.status is CheckStatus.FAIL
        assert "rcfailrepo" in result.detail
        assert str(script) in result.detail

    def test_healthy_repo_not_named_in_failure_detail(self, tmp_path: Path) -> None:
        dev_root = tmp_path / "dev"
        _make_hook_repo(dev_root, "brokenone", _BROKEN_INTERPRETER)
        _make_hook_repo(dev_root, "healthyone", sys.executable)

        result = check_hook_interpreters(dev_root=dev_root)

        assert result.status is CheckStatus.FAIL
        assert "brokenone" in result.detail
        assert "healthyone" not in result.detail

    def test_empty_dev_root_reports_ok_skip(self, tmp_path: Path) -> None:
        dev_root = tmp_path / "dev"
        dev_root.mkdir()

        result = check_hook_interpreters(dev_root=dev_root)

        assert result.status is CheckStatus.OK
        assert result.detail == _SKIP_NO_REPOS

    def test_missing_dev_root_reports_ok_skip(self, tmp_path: Path) -> None:
        result = check_hook_interpreters(dev_root=tmp_path / "does-not-exist")

        assert result.status is CheckStatus.OK
        assert result.detail == _SKIP_NO_REPOS

    def test_shared_interpreter_probed_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dev_root = tmp_path / "dev"
        _make_hook_repo(dev_root, "leftrepo", _BROKEN_INTERPRETER)
        _make_hook_repo(dev_root, "rightrepo", _BROKEN_INTERPRETER)
        _make_hook_repo(dev_root, "okrepo", sys.executable)

        calls: list[list[str]] = []
        real_run = subprocess.run

        def _counting_run(*args: object, **kwargs: object):
            argv = args[0] if args else kwargs.get("args")
            calls.append(list(argv))
            return real_run(*args, **kwargs)

        monkeypatch.setattr(capture_path, "subprocess", SimpleNamespace(run=_counting_run))

        result = check_hook_interpreters(dev_root=dev_root)

        assert result.status is CheckStatus.FAIL
        assert calls, "the check must probe interpreters through subprocess.run"
        assert sum(1 for argv in calls if argv[0] == _BROKEN_INTERPRETER) == 1
        assert sum(1 for argv in calls if argv[0] == sys.executable) == 1


class TestInstallFreshness:
    def test_identical_install_is_ok(self, tmp_path: Path) -> None:
        engine = _make_engine_repo(tmp_path)
        installed = _installed_copy(tmp_path)

        result = check_install_freshness(
            engine_root=engine, installed_package=installed
        )

        assert result.name == "install.freshness"
        assert result.status is CheckStatus.OK
        assert str(len(_ENGINE_FILES)) in result.detail

    def test_missing_file_fails(self, tmp_path: Path) -> None:
        engine = _make_engine_repo(tmp_path)
        installed = _installed_copy(tmp_path, skip="src/axon/hooks/git_hook.py")

        result = check_install_freshness(
            engine_root=engine, installed_package=installed
        )

        assert result.status is CheckStatus.FAIL
        assert "git_hook.py" in result.detail
        assert "pipx install --force" in result.suggestion

    def test_stale_file_fails(self, tmp_path: Path) -> None:
        engine = _make_engine_repo(tmp_path)
        installed = _installed_copy(tmp_path, mutate="src/axon/doctor/checks/capture.py")

        result = check_install_freshness(
            engine_root=engine, installed_package=installed
        )

        assert result.status is CheckStatus.FAIL
        assert "capture.py" in result.detail
        assert "pipx install --force" in result.suggestion

    def test_install_inside_checkout_skips(self, tmp_path: Path) -> None:
        engine = _make_engine_repo(tmp_path)
        installed = engine / "lib" / "python3.11" / "site-packages" / "axon"

        result = check_install_freshness(
            engine_root=engine, installed_package=installed
        )

        assert result.status is CheckStatus.OK
        assert result.detail == "skipped: running from the checkout"

    def test_engine_without_git_degrades_to_warn(self, tmp_path: Path) -> None:
        engine = tmp_path / "plain-dir"
        engine.mkdir()
        installed = _installed_copy(tmp_path)

        result = check_install_freshness(
            engine_root=engine, installed_package=installed
        )

        assert result.status is CheckStatus.WARN
        assert result.detail.startswith("skipped: ")

    def test_engine_without_axon_files_degrades_to_warn(self, tmp_path: Path) -> None:
        engine = tmp_path / "engine-no-axon"
        engine.mkdir()
        _git(engine, "init")
        _git(engine, "config", "user.email", "axon@example.com")
        _git(engine, "config", "user.name", "Axon Test")
        (engine / "README.md").write_text("not axon sources\n")
        _git(engine, "add", "-A")
        _git(engine, "commit", "-m", "empty of axon code")
        installed = _installed_copy(tmp_path)

        result = check_install_freshness(
            engine_root=engine, installed_package=installed
        )

        assert result.status is CheckStatus.WARN
        assert result.detail.startswith("skipped: ")


class TestCaptureGap:
    def test_recent_commit_hash_in_decisions_is_ok(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dev_root = tmp_path / "dev"
        hashes = _make_capture_repo(dev_root, "freshrepo", commits=3)
        oldest_of_last = hashes[0]
        _install_fake_store(monkeypatch, {"freshrepo": [None, oldest_of_last]})

        result = check_capture_gap(dev_root=dev_root)

        assert result.name == "capture.gap"
        assert result.status is CheckStatus.OK
        assert result.detail != _SKIP_NO_REPOS
        assert "1" in result.detail

    def test_no_recent_hash_fails_naming_repo(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dev_root = tmp_path / "dev"
        _make_capture_repo(dev_root, "gappedrepo", commits=2)
        _install_fake_store(monkeypatch, {"gappedrepo": ["deadbeef"]})

        result = check_capture_gap(dev_root=dev_root)

        assert result.status is CheckStatus.FAIL
        assert "gappedrepo" in result.detail
        # The suggestion no longer says "reinstall": the hooks were present and
        # working every time this check fired in production.
        assert "post-commit" in result.suggestion

    def test_repo_with_zero_decisions_is_gapped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dev_root = tmp_path / "dev"
        _make_capture_repo(dev_root, "silentrepo", commits=2)
        _install_fake_store(monkeypatch)

        result = check_capture_gap(dev_root=dev_root)

        assert result.status is CheckStatus.FAIL
        assert "silentrepo" in result.detail

    def test_healthy_repo_not_named_in_failure_detail(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dev_root = tmp_path / "dev"
        healthy_hashes = _make_capture_repo(dev_root, "healthyone", commits=2)
        _make_capture_repo(dev_root, "gappedone", commits=2)
        _install_fake_store(
            monkeypatch,
            {"healthyone": [healthy_hashes[-1]], "gappedone": ["deadbeef"]},
        )

        result = check_capture_gap(dev_root=dev_root)

        assert result.status is CheckStatus.FAIL
        assert "gappedone" in result.detail
        assert "healthyone" not in result.detail

    def test_store_failure_degrades_to_warn(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dev_root = tmp_path / "dev"
        _make_capture_repo(dev_root, "anyrepo", commits=1)
        _install_fake_store(monkeypatch, error=RuntimeError("pg down"))

        result = check_capture_gap(dev_root=dev_root)

        assert result.status is CheckStatus.WARN
        assert result.detail == "skipped: decision store unreachable"

    def test_repo_without_commits_is_skipped_not_gapped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dev_root = tmp_path / "dev"
        healthy = _make_capture_repo(dev_root, "healthyrepo", commits=1)
        _make_capture_repo(dev_root, "emptyrepo", commits=0)
        _install_fake_store(monkeypatch, {"healthyrepo": [healthy[0]]})

        result = check_capture_gap(dev_root=dev_root)

        assert result.status is CheckStatus.OK

    def test_empty_dev_root_reports_ok_skip(self, tmp_path: Path) -> None:
        dev_root = tmp_path / "dev"
        dev_root.mkdir()

        result = check_capture_gap(dev_root=dev_root)

        assert result.status is CheckStatus.OK
        assert result.detail == _SKIP_NO_REPOS


class TestRegistration:
    def test_run_all_checks_registers_the_three_new_checks(self) -> None:
        results = run_all_checks()
        by_name = {r.name: r for r in results}

        assert "capture.hook_interpreter" in by_name
        assert "install.freshness" in by_name
        assert "capture.gap" in by_name

        assert by_name["capture.hook_interpreter"].status is CheckStatus.OK
        assert by_name["capture.hook_interpreter"].detail == _SKIP_NO_REPOS
        assert by_name["capture.gap"].status is CheckStatus.OK
        assert by_name["capture.gap"].detail == _SKIP_NO_REPOS
        assert by_name["install.freshness"].status is CheckStatus.WARN
        assert by_name["install.freshness"].detail.startswith("skipped: ")


class TestCaptureGapDormantRepos:
    """A repo whose last commit predates the current hook install cannot have
    been captured by it. Reporting that as a capture failure is a false
    positive that never clears: on 2026-08-22 every hook was reinstalled, and
    four dormant repos have failed the check on every run since, while the
    hooks were provably healthy.
    """

    def _age_hook(self, dev_root: Path, name: str, *, newer_than_commits: bool) -> None:
        hook = dev_root / name / ".git" / "hooks" / "post-commit"
        head = subprocess.run(  # noqa: S603
            ["git", "-C", str(dev_root / name), "log", "-1", "--pretty=%ct"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        )
        last_commit = int(head.stdout.strip())
        offset = 3600 if newer_than_commits else -3600
        os.utime(hook, (last_commit + offset, last_commit + offset))

    def test_a_repo_with_no_commits_since_the_hook_was_installed_is_not_gapped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dev_root = tmp_path / "dev"
        _make_capture_repo(dev_root, "dormant", commits=2)
        self._age_hook(dev_root, "dormant", newer_than_commits=True)
        _install_fake_store(monkeypatch)

        result = check_capture_gap(dev_root=dev_root)

        assert result.status is not CheckStatus.FAIL, result.detail
        assert "dormant" in result.detail

    def test_a_repo_that_committed_after_the_hook_and_captured_nothing_is_still_gapped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dev_root = tmp_path / "dev"
        _make_capture_repo(dev_root, "reallybroken", commits=2)
        self._age_hook(dev_root, "reallybroken", newer_than_commits=False)
        _install_fake_store(monkeypatch)

        result = check_capture_gap(dev_root=dev_root)

        assert result.status is CheckStatus.FAIL
        assert "reallybroken" in result.detail

    def test_the_suggestion_does_not_send_you_to_reinstall_healthy_hooks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The old text said "re-run axon install-hooks" for every failure,
        which is the wrong move when the hook is present and working."""
        dev_root = tmp_path / "dev"
        _make_capture_repo(dev_root, "reallybroken", commits=2)
        self._age_hook(dev_root, "reallybroken", newer_than_commits=False)
        _install_fake_store(monkeypatch)

        result = check_capture_gap(dev_root=dev_root)

        assert "post-commit" in result.suggestion


def test_doctor_no_longer_reports_recall_coverage(tmp_path: Path) -> None:
    """recall.coverage measured adherence to a ritual (search before read), not
    a result. It sat at 4.7% while recall.savings sat at 84%, and the one
    intervention that could move it - injecting a search before every read -
    would have driven it to 100% while saving nothing, because PreToolUse
    cannot cancel the Read that follows. recall.savings, measured over real
    usage, is the honest signal and stays.
    """
    from axon.doctor import run_all_checks

    names = {result.name for result in run_all_checks(data_root=tmp_path)}

    assert "recall.coverage" not in names
    assert "recall.savings" in names
