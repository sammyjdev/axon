"""Session-note keys must follow the parent git repo, not basename(cwd).

A note filed from a linked worktree must be keyed by repo_identity(cwd),
so that get_session_memory(project=<repo>) can find it. A cwd outside any
git repo keeps the basename fallback it always had.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import asyncpg
import pytest

from axon.cli import pb
from axon.mcp import server
from axon.store.session_store import SessionStore

REPO_DIRNAME = "session-note-repo"
WORKTREE_DIRNAME = "agent-worktree-note"


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(  # noqa: S603
        ["git", *args], cwd=cwd, check=True, capture_output=True  # noqa: S603, S607
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo_dir = tmp_path / REPO_DIRNAME
    repo_dir.mkdir()
    _git(["init", "-b", "main"], repo_dir)
    # The suite isolates global git config (tests/conftest.py); identity is
    # still required for the seed commit, so set it locally in the temp repo.
    _git(["config", "user.email", "test@axon.dev"], repo_dir)
    _git(["config", "user.name", "AXON Test"], repo_dir)
    (repo_dir / "README.md").write_text(f"repo at {repo_dir}\n", encoding="utf-8")
    _git(["add", "."], repo_dir)
    _git(["commit", "-m", "test: seed the repository"], repo_dir)
    return repo_dir


@pytest.fixture
def worktree(repo: Path, tmp_path: Path) -> Path:
    worktree_dir = tmp_path / WORKTREE_DIRNAME
    _git(["worktree", "add", "-b", "note-branch", str(worktree_dir)], repo)
    return worktree_dir


def _capture_session_note(monkeypatch: pytest.MonkeyPatch) -> list:
    """Stand in for the store so no live database is touched."""
    saved = []

    class _Store:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def init(self) -> None:
            return None

        async def save_note(self, note) -> int:
            saved.append(note)
            return 1

    monkeypatch.setattr("axon.store.session_store.SessionStore", _Store)
    return saved


def _get_pg_url() -> str:
    env = os.environ.get("AXON_PG_URL")
    if env:
        return env
    from axon.config.runtime import load_runtime_config

    return load_runtime_config().pg_url


def _skip_without_postgres() -> None:
    """Skip when no Postgres is reachable."""
    async def _probe() -> None:
        con = await asyncpg.connect(_get_pg_url())
        await con.close()

    try:
        asyncio.run(_probe())
    except (OSError, asyncpg.PostgresError):
        pytest.skip("no reachable Postgres; round-trip test writes to SessionStore")


def test_session_note_in_a_worktree_keys_under_the_parent_repo(
    worktree: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    saved = _capture_session_note(monkeypatch)
    monkeypatch.chdir(worktree)

    pb.session_note("a quick note from worktree")

    assert len(saved) == 1, "the note was never persisted"
    assert saved[0].project == REPO_DIRNAME
    assert saved[0].project != WORKTREE_DIRNAME, "still keyed by the worktree basename"
    assert f"Nota salva em '{REPO_DIRNAME}'" in capsys.readouterr().out


def test_session_note_in_a_worktree_subdirectory_keys_under_the_parent_repo(
    worktree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subdirectory = worktree / "src" / "pkg"
    subdirectory.mkdir(parents=True)
    saved = _capture_session_note(monkeypatch)
    monkeypatch.chdir(subdirectory)

    pb.session_note("note from worktree subdirectory")

    assert len(saved) == 1
    assert saved[0].project == REPO_DIRNAME


def test_the_top_level_note_alias_keys_under_the_parent_repo(
    worktree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved = _capture_session_note(monkeypatch)
    monkeypatch.chdir(worktree)

    pb.note("note via top-level alias")

    assert len(saved) == 1
    assert saved[0].project == REPO_DIRNAME


def test_session_note_outside_any_repo_keeps_the_basename_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "scratch-dir"
    outside.mkdir()
    saved = _capture_session_note(monkeypatch)
    monkeypatch.chdir(outside)

    pb.session_note("note outside any git repo")

    assert len(saved) == 1
    assert saved[0].project == "scratch-dir"


def test_a_note_filed_from_a_worktree_is_visible_to_get_session_memory(
    worktree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _skip_without_postgres()
    monkeypatch.chdir(worktree)
    monkeypatch.setattr(server, "_get_session_store", lambda: SessionStore())

    note_text = "note recorded in worktree for round-trip test"
    pb.session_note(note_text)

    result = asyncio.run(server.get_session_memory(project=REPO_DIRNAME))
    assert note_text in result
    assert "## Notas de sessão" in result
