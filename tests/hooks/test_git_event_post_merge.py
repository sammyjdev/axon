"""Tests for post-merge range scan (dec-110 cloud-arm bridge)."""

from __future__ import annotations

import subprocess
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from axon.hooks.git_event import _scan_pulled_range
from axon.store.session_store import SessionStore


@pytest.fixture
async def store(tmp_path: Path) -> AsyncGenerator[SessionStore, None]:
    s = SessionStore(db_path=tmp_path / "axon.db")
    await s.init()
    yield s
    await s.close()


def _run(cwd: Path, *args: str) -> str:
    return subprocess.run(
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
def pulled_repo(tmp_path, monkeypatch):
    """origin gains one signal + one plain commit; clone pulls them.

    Signal commits can trigger a real ADR inference call (live LLM access is
    configured in some dev environments), which falls through to
    axon.adr.draft_pool.write_draft() on a promotion-gate rejection. That
    resolves its write location via data_root(), which reads $AXON_DATA_ROOT
    or else defaults to the real process CWD's .axon/ -- pin it to a
    tmp-scoped dir so tests never leak drafts into the real repo tree.
    """
    monkeypatch.setenv("AXON_DATA_ROOT", str(tmp_path / ".axon"))
    origin = tmp_path / "origin"
    _git_init(origin)
    _commit(origin, "base.txt", "chore: base")
    clone = tmp_path / "clone"
    _run(tmp_path, "git", "clone", str(origin), str(clone))
    _run(clone, "git", "config", "user.email", "t@t")
    _run(clone, "git", "config", "user.name", "t")
    signal_hash = _commit(origin, "a.txt", "arch: adopt widget pattern")
    plain_hash = _commit(origin, "b.txt", "chore: bump dep")
    _run(clone, "git", "pull")
    return clone, signal_hash, plain_hash


@pytest.mark.asyncio
async def test_scan_captures_only_signal_commits(pulled_repo, store):
    clone, signal_hash, plain_hash = pulled_repo

    await _scan_pulled_range(store=store, cwd=clone)

    assert await store.find_decision_by_git_hash(signal_hash, repo=clone.name)
    assert not await store.find_decision_by_git_hash(plain_hash, repo=clone.name)


@pytest.mark.asyncio
async def test_scan_is_idempotent(pulled_repo, store):
    clone, signal_hash, _ = pulled_repo

    await _scan_pulled_range(store=store, cwd=clone)
    first = await store.find_decision_by_git_hash(signal_hash, repo=clone.name)
    assert first is not None

    await _scan_pulled_range(store=store, cwd=clone)

    # second pass must not raise nor duplicate: exactly one decision row
    # for this git_hash, and it must be the same row (not a new insert).
    matching = [
        d
        for d in await store.find_decisions_by_repo(clone.name, limit=50)
        if d.git_hash == signal_hash
    ]
    assert len(matching) == 1
    assert matching[0].id == first.id


@pytest.mark.asyncio
async def test_scan_no_orig_head_is_noop(tmp_path, store):
    repo = tmp_path / "fresh"
    _git_init(repo)
    _commit(repo, "a.txt", "arch: something")
    from axon.hooks.git_event import _scan_pulled_range

    await _scan_pulled_range(store=store, cwd=repo)
