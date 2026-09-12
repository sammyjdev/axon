"""Regression tests for issue #185: doctor only discovers repos under ~/dev.

`_onboarded_repos` found onboarded repos with two fixed-depth globs under
`dev_root`, so an onboarded repo outside that tree (like ~/.claude) was
invisible to the capture-path checks: its hook pointed at a renamed venv for
a month while doctor kept reporting every repo healthy. The fix records
every repo the installer touches in a registry anchored at
`load_runtime_config().data_root` and unions it into discovery, keeping the
glob as fallback for pre-registry installs.

Registry contract under test: `install_hooks(repo)` records the repo root
(absolute, resolved) as one string in
`load_runtime_config().data_root / "onboarded_repos.json"`, a plain JSON
list. Recording is best-effort: a failing registry write never breaks the
install.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from axon.doctor import CheckStatus
from axon.doctor.checks import capture_path
from axon.doctor.checks.capture_path import (
    _onboarded_repos,
    check_capture_gap,
    check_hook_interpreters,
)
from axon.hooks.git_installer import _BEGIN, _END, install_hooks

_BROKEN_INTERPRETER = "/nonexistent/python"
_SKIP_NO_REPOS = "skipped: no onboarded repos"
_REGISTRY_NAME = "onboarded_repos.json"


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Resolved once: registry entries are recorded resolved, and glob paths
    # must compare equal to them.
    tmp = tmp_path.resolve()
    monkeypatch.delenv("AXON_DEV_ROOT", raising=False)
    monkeypatch.setenv("AXON_ENGINE", str(tmp / "engine"))
    monkeypatch.setenv("AXON_DATA_ROOT", str(tmp / "data"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    return tmp


def _registry_path(tmp: Path) -> Path:
    return tmp / "engine" / "data" / _REGISTRY_NAME


def _read_registry(tmp: Path) -> list[str]:
    return json.loads(_registry_path(tmp).read_text(encoding="utf-8"))


def _write_registry(tmp: Path, repos: list[Path]) -> None:
    target = _registry_path(tmp)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps([str(repo) for repo in repos]), encoding="utf-8")


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


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init")
    _git(path, "config", "user.email", "axon@example.com")
    _git(path, "config", "user.name", "Axon Test")
    return path


def _hook_text(interpreter: str) -> str:
    return (
        "#!/usr/bin/env bash\n"
        f"{_BEGIN}\n"
        f"{shlex.quote(interpreter)} -m axon.hooks.git_event commit"
        " 2>/dev/null || true\n"
        f"{_END}\n"
    )


def _make_hook_repo(path: Path, interpreter: str) -> Path:
    repo = _init_repo(path)
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "post-commit").write_text(_hook_text(interpreter), encoding="utf-8")
    return repo


def _make_capture_repo(path: Path, commits: int) -> list[str]:
    """A real git repo carrying an AXON post-commit hook.

    Returns the commit hashes, newest last. Content is seeded with the repo's
    full path: constant content across two fixture repos yields identical
    commit hashes within the same clock second (#146, this same check family).
    """
    repo = _init_repo(path)
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "post-commit").write_text(_hook_text(sys.executable), encoding="utf-8")
    hashes: list[str] = []
    for i in range(commits):
        (repo / f"f{i}.txt").write_text(f"{repo} content {i}\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", f"commit {i}")
        hashes.append(_head(repo))
    return hashes


def _install_fake_store(
    monkeypatch: pytest.MonkeyPatch, captured: set[str] | None = None
) -> None:
    captured_hashes = captured or set()

    class _FakeSessionStore:
        def __init__(self, db_path: object) -> None:
            self.db_path = db_path

        async def init(self) -> None:
            return None

        async def close(self) -> None:
            return None

        async def find_decision_by_git_hash(
            self, git_hash: str, *, repo: str | None = None
        ) -> object | None:
            if git_hash in captured_hashes:
                return SimpleNamespace(git_hash=git_hash)
            return None

    monkeypatch.setattr(capture_path, "SessionStore", _FakeSessionStore)


class TestInstallHooksRecordsRegistry:
    @pytest.mark.skipif(os.name == "nt", reason="creating a symlink needs privileges")
    def test_records_resolved_absolute_repo_root(self, isolated_env: Path) -> None:
        real = _init_repo(isolated_env / "dev" / "realrepo")
        link = isolated_env / "dev" / "linkrepo"
        link.symlink_to(real, target_is_directory=True)

        install_hooks(link)

        assert _registry_path(isolated_env).is_file()
        entries = _read_registry(isolated_env)
        assert entries == [str(real)]
        assert "linkrepo" not in entries[0]

    def test_second_install_on_the_noop_path_still_records(
        self, isolated_env: Path
    ) -> None:
        repo = _init_repo(isolated_env / "dev" / "plainrepo")

        install_hooks(repo)
        second = install_hooks(repo)

        assert second == []  # documents that we are on the idempotent no-op path
        entries = _read_registry(isolated_env)
        assert entries.count(str(repo)) == 1

    def test_each_installed_repo_gets_one_entry(self, isolated_env: Path) -> None:
        first = _init_repo(isolated_env / "dev" / "alpha")
        second = _init_repo(isolated_env / "elsewhere" / "beta")

        install_hooks(first)
        install_hooks(second)

        entries = _read_registry(isolated_env)
        assert len(entries) == 2
        assert sorted(entries) == sorted([str(first), str(second)])

    def test_registry_write_failure_never_breaks_the_install(
        self, isolated_env: Path
    ) -> None:
        data_root = isolated_env / "engine" / "data"
        data_root.parent.mkdir(parents=True, exist_ok=True)
        # A plain file where data/ should be makes every registry write fail.
        data_root.write_text("not a directory\n", encoding="utf-8")
        repo = _init_repo(isolated_env / "dev" / "hookrepo")

        installed = install_hooks(repo)

        assert "post-commit" in installed
        hook = repo / ".git" / "hooks" / "post-commit"
        assert _BEGIN in hook.read_text(encoding="utf-8")

    def test_invalid_registry_is_not_rewritten_erasing_recorded_repos(
        self, isolated_env: Path
    ) -> None:
        """A registry that exists but cannot be parsed must be left alone.

        Round-2 defect: a registry truncated by a crash mid-write (or
        hand-edited) reset `entries` to [], so the next install for ANY repo
        rewrote the file holding only that repo - every previously recorded
        repo was erased silently. For repos the two-depth glob cannot see
        (~/.claude), erasing the registry entry is a fall to invisible, not
        to glob-only. The fix: registry present but invalid -> skip
        recording, leave the existing bytes untouched.
        """
        repo_a = _init_repo(isolated_env / "dotclaude" / "repoa")
        repo_b = _init_repo(isolated_env / "dev" / "repob")
        install_hooks(repo_a)
        assert _read_registry(isolated_env) == [str(repo_a)]

        # Present but unparseable: the recorded list survives as the leading
        # JSON document, the trailing bytes are the crash garbage.
        corrupt = json.dumps([str(repo_a)]) + '{"truncated": '
        _registry_path(isolated_env).write_text(corrupt, encoding="utf-8")

        install_hooks(repo_b)

        raw = _registry_path(isolated_env).read_text(encoding="utf-8")
        assert raw == corrupt  # install_hooks(repo_b) must not rewrite it
        entries, _ = json.JSONDecoder().raw_decode(raw)
        assert entries == [str(repo_a)]


class TestDiscoveryUnionsRegistryAndGlob:
    def test_registry_repo_outside_dev_root_is_discovered(
        self, isolated_env: Path
    ) -> None:
        dev_root = isolated_env / "dev"
        dev_root.mkdir(parents=True)
        outside = _make_hook_repo(
            isolated_env / "dotclaude" / "treerepo", sys.executable
        )
        _write_registry(isolated_env, [outside])

        repos = _onboarded_repos(dev_root)

        assert repos == [outside]

    def test_union_dedupes_repos_found_by_both_and_sorts(
        self, isolated_env: Path
    ) -> None:
        dev_root = isolated_env / "dev"
        _make_hook_repo(dev_root / "aaaglob", sys.executable)
        both_repo = _make_hook_repo(dev_root / "bbbboth", sys.executable)
        registry_repo = _make_hook_repo(
            isolated_env / "zzzout" / "cccreg", sys.executable
        )
        _write_registry(isolated_env, [both_repo, registry_repo])

        repos = _onboarded_repos(dev_root)

        assert [repo.name for repo in repos] == ["aaaglob", "bbbboth", "cccreg"]
        assert repos.count(both_repo) == 1

    def test_registry_entry_with_dotdot_spelling_dedupes_to_canonical(
        self, isolated_env: Path
    ) -> None:
        """A registry entry that spells the repo non-canonically (with `..`)
        must dedupe against the same repo found by the glob and come back in
        one resolved spelling (#185: DROP_PATH_RESOLVE survived the suite)."""
        dev_root = isolated_env / "dev"
        repo = _make_hook_repo(dev_root / "myrepo", sys.executable)
        _write_registry(isolated_env, [dev_root / "myrepo" / ".." / "myrepo"])

        repos = _onboarded_repos(dev_root)

        assert len(repos) == 1
        assert repos[0] == repo.resolve()

    def test_stale_registry_entries_are_silently_dropped(
        self, isolated_env: Path
    ) -> None:
        dev_root = isolated_env / "dev"
        dev_root.mkdir(parents=True)
        missing = isolated_env / "gone" / "deletedrepo"
        no_hook = _init_repo(isolated_env / "elsewhere" / "nohookrepo")
        hook = no_hook / ".git" / "hooks" / "post-commit"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/usr/bin/env bash\necho foreign\n", encoding="utf-8")
        _write_registry(isolated_env, [missing, no_hook])

        repos = _onboarded_repos(dev_root)

        assert repos == []

    def test_discovery_is_inert_without_a_registry_file(
        self, isolated_env: Path
    ) -> None:
        dev_root = isolated_env / "dev"
        dev_root.mkdir(parents=True)
        assert not _registry_path(isolated_env).exists()

        assert _onboarded_repos(dev_root) == []

        result = check_hook_interpreters(dev_root=dev_root)
        assert result.status is CheckStatus.OK
        assert result.detail == _SKIP_NO_REPOS

    def test_glob_fallback_still_discovers_pre_registry_repos(
        self, isolated_env: Path
    ) -> None:
        dev_root = isolated_env / "dev"
        glob_repo = _make_hook_repo(dev_root / "legacyrepo", sys.executable)
        assert not _registry_path(isolated_env).exists()

        repos = _onboarded_repos(dev_root)

        assert repos == [glob_repo]


class TestChecksSeeRegistryRepos:
    def test_hook_interpreters_fails_on_registry_only_repo(
        self, isolated_env: Path
    ) -> None:
        dev_root = isolated_env / "dev"
        dev_root.mkdir(parents=True)
        outside = _make_hook_repo(
            isolated_env / "dotclaude" / "claudehooks", _BROKEN_INTERPRETER
        )
        _write_registry(isolated_env, [outside])

        result = check_hook_interpreters(dev_root=dev_root)

        assert result.name == "capture.hook_interpreter"
        assert result.status is CheckStatus.FAIL
        assert "claudehooks" in result.detail
        assert _BROKEN_INTERPRETER in result.detail

    def test_capture_gap_reports_gapped_registry_repo_outside_dev_root(
        self, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dev_root = isolated_env / "dev"
        dev_root.mkdir(parents=True)
        repo = isolated_env / "dotclaude" / "gappedrepo"
        _make_capture_repo(repo, commits=2)
        _write_registry(isolated_env, [repo])
        _install_fake_store(monkeypatch)

        result = check_capture_gap(dev_root=dev_root)

        assert result.name == "capture.gap"
        assert result.status is CheckStatus.FAIL
        assert "gappedrepo" in result.detail

    def test_capture_gap_passes_registry_repo_whose_commits_are_captured(
        self, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dev_root = isolated_env / "dev"
        dev_root.mkdir(parents=True)
        repo = isolated_env / "dotclaude" / "healthyrepo"
        hashes = _make_capture_repo(repo, commits=2)
        _write_registry(isolated_env, [repo])
        _install_fake_store(monkeypatch, captured={hashes[-1]})

        result = check_capture_gap(dev_root=dev_root)

        assert result.status is CheckStatus.OK
        assert result.detail != _SKIP_NO_REPOS
        assert "healthyrepo" not in result.detail

    def test_doctor_sees_a_repo_installed_outside_dev_root(
        self, isolated_env: Path
    ) -> None:
        """The issue's exact scenario: the repo was onboarded long ago (so it
        is in the registry), and its baked interpreter has since died."""
        dev_root = isolated_env / "dev"
        dev_root.mkdir(parents=True)
        repo = _init_repo(isolated_env / "dotclaude" / "clauderepo")
        install_hooks(repo)
        hook = repo / ".git" / "hooks" / "post-commit"
        hook.write_text(_hook_text(_BROKEN_INTERPRETER), encoding="utf-8")

        result = check_hook_interpreters(dev_root=dev_root)

        assert result.status is CheckStatus.FAIL
        assert "clauderepo" in result.detail


class TestCaptureGapLabels:
    def test_gapped_repo_inside_dev_root_is_labeled_relative_to_dev_root(
        self, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A gapped repo nested INSIDE dev_root is labeled by its path
        relative to dev_root, exactly (#185: IDENTITY_RETURN on _repo_label
        survived because the basename is a substring of the absolute path
        too, so every substring assertion held under both spellings)."""
        dev_root = isolated_env / "dev"
        repo = dev_root / "group" / "gappedrepo"
        _make_capture_repo(repo, commits=2)
        _install_fake_store(monkeypatch)

        result = check_capture_gap(dev_root=dev_root)

        assert result.name == "capture.gap"
        assert result.status is CheckStatus.FAIL
        assert result.detail == "1 of 1 repo(s) gapped: group/gappedrepo"
