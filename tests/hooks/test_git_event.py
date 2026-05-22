"""Tests for AXON git event handlers (T3.2)."""

from __future__ import annotations

import subprocess
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from axon.hooks.git_event import main, on_commit, on_init, on_push
from axon.store.session_store import SessionStore


@pytest.fixture
async def store(tmp_path: Path) -> AsyncGenerator[SessionStore, None]:
    s = SessionStore(db_path=tmp_path / "axon.db")
    await s.init()
    yield s
    await s.close()


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


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


async def test_on_commit_captures_draft_decision(
    store: SessionStore, git_repo: Path
) -> None:
    decision_id = await on_commit(store=store, cwd=git_repo)
    assert decision_id == "dec-001"

    found = await store.find_decisions_by_repo("myrepo")
    assert len(found) == 1
    decision = found[0]
    assert decision.summary == "feat: add the entry point"
    assert decision.status == "draft"
    assert decision.agent == "manual"
    assert Path("main.py") in decision.files
    assert decision.git_hash is not None and len(decision.git_hash) == 40


async def test_on_commit_truncates_long_subject(
    store: SessionStore, git_repo: Path
) -> None:
    _git(["commit", "--allow-empty", "-m", "x" * 120], git_repo)
    decision_id = await on_commit(store=store, cwd=git_repo)
    found = [d for d in await store.find_decisions_by_repo("myrepo") if d.id == decision_id]
    assert len(found[0].summary) == 80


async def test_on_commit_detects_agent_from_env(
    store: SessionStore, git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AXON_AGENT", "codex")
    decision_id = await on_commit(store=store, cwd=git_repo)
    found = [d for d in await store.find_decisions_by_repo("myrepo") if d.id == decision_id]
    assert found[0].agent == "codex"


async def test_on_commit_rejects_unknown_agent(
    store: SessionStore, git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AXON_AGENT", "some-bot")
    await on_commit(store=store, cwd=git_repo)
    found = await store.find_decisions_by_repo("myrepo")
    assert found[0].agent == "manual"  # unknown agent falls back to manual


async def test_on_push_and_on_init_are_safe_stubs(store: SessionStore) -> None:
    assert await on_push(store=store) is None
    assert await on_init(store=store) is None


def test_main_unknown_event_returns_zero() -> None:
    assert main(["bogus"]) == 0
    assert main([]) == 0
