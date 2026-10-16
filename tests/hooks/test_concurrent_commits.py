"""Commits captured at the same moment each keep their own decision.

The id was `MAX + 1` read on one round trip and written with an upsert on the
next, so two hooks that read the same MAX wrote the same id and the second
silently replaced the first. Measured 2026-10-15: 8 concurrent commit hooks
against a fresh database left 7 rows.
"""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from axon.hooks.git_event import on_commit
from axon.store.session_store import SessionStore


@pytest.fixture
async def store(tmp_path: Path) -> AsyncGenerator[SessionStore, None]:
    s = SessionStore()
    await s.init()
    yield s
    await s.close()


def _git(args: list[str], repo: Path) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)  # noqa: S603, S607


def _repo_with_one_commit(root: Path, name: str) -> Path:
    repo = root / name
    repo.mkdir()
    _git(["init", "-q"], repo)
    _git(["config", "user.email", "test@axon.dev"], repo)
    _git(["config", "user.name", "AXON Test"], repo)
    (repo / "f.txt").write_text(name, encoding="utf-8")
    _git(["add", "."], repo)
    _git(["commit", "-q", "-m", f"decision: change in {name}"], repo)
    return repo


async def test_commits_captured_at_the_same_moment_each_keep_their_decision(
    store: SessionStore, tmp_path: Path
) -> None:
    repos = [_repo_with_one_commit(tmp_path, f"repo{i}") for i in range(6)]

    ids = await asyncio.gather(*(on_commit(store=store, cwd=repo) for repo in repos))

    saved = await store.all_decisions()
    assert len(set(ids)) == 6
    assert sorted(d.id for d in saved) == sorted(ids)
