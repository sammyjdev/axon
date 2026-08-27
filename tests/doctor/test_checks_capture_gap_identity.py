"""Tests for capture.gap identity and uniqueness (issue #146).

These tests verify that capture.gap uses the global commit hash for identity
rather than the repository basename, preventing collisions between repos with
the same name in different paths and allowing renamed repos to be recognized.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from axon.doctor import CheckStatus
from axon.doctor.checks import capture_path
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


def _make_capture_repo(dev_root: Path, name: str, commits: int) -> list[str]:
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


def _install_fake_hash_store(
    monkeypatch: pytest.MonkeyPatch,
    hash_to_repo: dict[str, str] | None = None,
    error: Exception | None = None,
) -> None:
    stored = hash_to_repo or {}

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
            if error is not None:
                raise error
            if git_hash not in stored:
                return None
            stored_repo = stored[git_hash]
            if repo is not None and repo != stored_repo:
                return None
            return SimpleNamespace(git_hash=git_hash, repo=stored_repo)

    monkeypatch.setattr(capture_path, "SessionStore", _FakeSessionStore)


def test_repo_renamed_on_disk_is_not_gapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dev_root = tmp_path / "dev"
    # Repo was called 'PitStopOS' when decision was captured
    hashes = _make_capture_repo(dev_root, "pitstop-os", commits=3)
    # Decision recorded under old name
    _install_fake_hash_store(monkeypatch, {hashes[-1]: "PitStopOS"})

    result = capture_path.check_capture_gap(dev_root=dev_root)

    assert result.status is CheckStatus.OK
    assert "pitstop-os" not in result.detail


def test_same_basename_under_different_groups_are_judged_independently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dev_root = tmp_path / "dev"
    products = _make_capture_repo(dev_root / "products", "foo", commits=2)
    _tools = _make_capture_repo(dev_root / "tools", "foo", commits=2)
    # Only the 'products/foo' repo has a decision
    _install_fake_hash_store(monkeypatch, {products[-1]: "foo"})

    result = capture_path.check_capture_gap(dev_root=dev_root)

    assert result.status is CheckStatus.FAIL
    # 1 of 2 gapped proves they were treated as distinct entities
    assert "1 of 2 repo(s) gapped" in result.detail


def test_repo_with_no_decision_anywhere_still_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dev_root = tmp_path / "dev"
    _make_capture_repo(dev_root, "gnomon-eval", commits=3)
    # Decisions exist, but not for gnomon-eval's actual commits
    _install_fake_hash_store(
        monkeypatch,
        {"deadbeef": "gnomon-eval", "cafebabe": "gnomon-eval-issue-8"},
    )

    result = capture_path.check_capture_gap(dev_root=dev_root)

    assert result.status is CheckStatus.FAIL
    assert "gnomon-eval" in result.detail
    # The suggestion no longer says "reinstall": the hooks were present and
    # working every time this check fired in production.
    assert "post-commit" in result.suggestion


def test_store_failure_on_hash_lookup_degrades_to_warn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dev_root = tmp_path / "dev"
    _make_capture_repo(dev_root, "anyrepo", commits=1)
    _install_fake_hash_store(monkeypatch, error=RuntimeError("pg down"))

    result = capture_path.check_capture_gap(dev_root=dev_root)

    assert result.status is CheckStatus.WARN
    assert result.detail == "skipped: decision store unreachable"
