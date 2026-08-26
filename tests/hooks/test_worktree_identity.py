"""Regression coverage for stable repository identity in linked worktrees."""

from __future__ import annotations

import subprocess
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from axon.core.decision import Decision
from axon.hooks.file_bridge import update_context_file
from axon.hooks.git_event import _scan_pulled_range, on_commit, on_push
from axon.store.session_store import SessionStore


@pytest.fixture
async def store(tmp_path: Path) -> AsyncGenerator[SessionStore, None]:
    s = SessionStore(db_path=tmp_path / "axon.db")
    await s.init()
    yield s
    await s.close()


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)  # noqa: S603, S607


def _run(cwd: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603
        args, cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def _git_init(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _run(path, "git", "init", "-b", "main")
    _run(path, "git", "config", "user.email", "t@t")
    _run(path, "git", "config", "user.name", "t")


def _commit(path: Path, fname: str, msg: str) -> str:
    (path / fname).write_text("x")
    _run(path, "git", "add", ".")
    _run(path, "git", "commit", "-m", msg)
    return _run(path, "git", "log", "-1", "--pretty=%H").strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _git(["init"], repo)
    _git(["config", "user.email", "test@axon.dev"], repo)
    _git(["config", "user.name", "AXON Test"], repo)
    (repo / "main.py").write_text("print('hello')\n", encoding="utf-8")
    _git(["add", "."], repo)
    _git(["commit", "-m", "feat: add the entry point"], repo)
    return repo


@pytest.fixture
def worktree(git_repo: Path, tmp_path: Path) -> Path:
    wt = tmp_path / "agent-issue-158"
    _git(["worktree", "add", "-b", "issue-158", str(wt)], git_repo)
    return wt


def _decision(**overrides: Any) -> Decision:
    base: dict[str, Any] = dict(
        id="dec-001",
        timestamp=datetime(2026, 5, 1, tzinfo=UTC),
        agent="claude-code",
        repo="myrepo",
        summary="a decision",
    )
    base.update(overrides)
    return Decision(**base)


async def test_on_commit_from_a_worktree_files_under_the_parent_repo_key(
    store: SessionStore, tmp_path: Path, worktree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AXON_DATA_ROOT", str(tmp_path / ".axon"))
    (worktree / "feature.py").write_text("feature = True\n", encoding="utf-8")
    _git(["add", "."], worktree)
    _git(["commit", "-m", "feat: add feature"], worktree)
    sha = subprocess.check_output(  # noqa: S603
        ["git", "-C", str(worktree), "log", "-1", "--pretty=%H"], text=True  # noqa: S603, S607
    ).strip()

    decision_id = await on_commit(store=store, cwd=worktree)

    found = [
        decision
        for decision in await store.find_decisions_by_repo("myrepo")
        if decision.git_hash == sha
    ]
    assert decision_id is not None
    assert len(found) == 1
    assert found[0].repo == "myrepo"
    assert await store.find_decisions_by_repo("agent-issue-158") == []


async def test_on_push_from_a_worktree_counts_the_parent_repos_decisions(
    store: SessionStore, tmp_path: Path, worktree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AXON_MILESTONE", raising=False)
    for number in range(1, 11):
        await store.save_decision(
            Decision(
                id=f"dec-{number:03}",
                timestamp=datetime(2026, 5, number, tzinfo=UTC),
                agent="manual",
                repo="myrepo",
                summary=f"decision {number}",
            )
        )
    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)

    async def no_judge(decision: Decision, context: str = "") -> None:
        return None

    monkeypatch.setattr("axon.hooks.git_event.discover_vault", lambda: vault)
    monkeypatch.setattr("axon.hooks.git_event.score_decision", no_judge)

    await on_push(store=store, cwd=worktree)

    assert (vault / "AXON" / "Architecture" / "myrepo.md").exists()
    assert not (vault / "AXON" / "Architecture" / "agent-issue-158.md").exists()
    assert (vault / "AXON" / "Journal" / "dec-001.md").exists()


async def test_scan_pulled_range_from_a_worktree_files_under_the_parent_repo_key(
    tmp_path: Path, store: SessionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AXON_DATA_ROOT", str(tmp_path / ".axon"))
    repo = tmp_path / "myrepo"
    _git_init(repo)
    base_hash = _commit(repo, "base.py", "chore: base")
    worktree = tmp_path / "agent-issue-158"
    _run(repo, "git", "worktree", "add", "-b", "issue-158", str(worktree))
    signal_hash = _commit(worktree, "widget.py", "arch: adopt widget pattern")
    _run(worktree, "git", "update-ref", "ORIG_HEAD", base_hash)

    await _scan_pulled_range(store=store, cwd=worktree)

    assert await store.find_decision_by_git_hash(signal_hash, repo="myrepo") is not None
    assert await store.find_decision_by_git_hash(signal_hash, repo="agent-issue-158") is None


async def test_context_file_in_a_worktree_lists_the_parent_repos_decisions(
    store: SessionStore, worktree: Path
) -> None:
    await store.save_decision(_decision(id="dec-001", repo="myrepo", summary="rename to axon"))

    target = await update_context_file(worktree, store=store)

    text = target.read_text(encoding="utf-8")
    assert target == worktree / ".axon" / "context.md"
    assert text.splitlines()[0].endswith("myrepo")
    assert "dec-001" in text
    assert "None captured yet" not in text
