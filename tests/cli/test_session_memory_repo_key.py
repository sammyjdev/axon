"""Session-memory keys must follow the parent git repo, not basename(cwd) (#186).

The Stop/PostCompact hooks derive the project key with os.path.basename(cwd),
so a session lived in a linked worktree never joins the stream that
get_session_memory(project=<repo>) reads. Keying by repo_identity(cwd) is the
fix; a cwd outside any git repo keeps the basename fallback it always had.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from axon.cli import pb
from axon.memory.session_compressor import SessionCompressor

REPO_DIRNAME = "session-key-repo"
WORKTREE_DIRNAME = "agent-issue-186"


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
    _git(["worktree", "add", "-b", "issue-186", str(worktree_dir)], repo)
    return worktree_dir


def _turns_transcript(path: Path) -> Path:
    path.write_text(
        "".join(
            json.dumps({"type": role, "message": {"role": role, "content": content}})
            + "\n"
            for role, content in (
                ("user", "why does the worktree session never join the repo stream?"),
                ("assistant", "it is filed under the worktree basename"),
            )
        ),
        encoding="utf-8",
    )
    return path


def _compact_transcript(path: Path, summary: str) -> Path:
    path.write_text(
        json.dumps(
            {
                "type": "user",
                "isCompactSummary": True,
                "message": {"role": "user", "content": [{"type": "text", "text": summary}]},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _capture_session_save(monkeypatch: pytest.MonkeyPatch) -> list:
    """Stand in for the store so no live database is touched, and make
    compression deterministic: the key under test is the only variable."""
    saved = []

    class _Store:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def init(self) -> None:
            return None

        async def save_session_memory(self, memory) -> int:
            saved.append(memory)
            return 1

    monkeypatch.setattr("axon.store.session_store.SessionStore", _Store)

    async def _compress(_self: SessionCompressor) -> str:
        return "a faithful summary"

    monkeypatch.setattr(SessionCompressor, "compress", _compress)
    return saved


def _run_compact_hook(
    monkeypatch: pytest.MonkeyPatch, transcript: Path, cwd: Path
) -> tuple[list, None]:
    saved: list = []
    monkeypatch.setattr(pb, "_save_compact_summary", lambda **kw: saved.append(kw))
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(json.dumps({"transcript_path": str(transcript), "cwd": str(cwd)})),
    )
    pb.compact_hook()
    return saved, None


def test_session_save_in_a_worktree_keys_under_the_parent_repo(
    repo: Path,
    worktree: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    transcript = _turns_transcript(tmp_path / "session.jsonl")
    saved = _capture_session_save(monkeypatch)

    pb.session_save(cwd=str(worktree), transcript=str(transcript))

    assert len(saved) == 1, "the session was never persisted"
    assert saved[0].project == REPO_DIRNAME
    assert saved[0].project != WORKTREE_DIRNAME, "still keyed by the worktree basename"
    assert f"Session memory salva: {REPO_DIRNAME}" in capsys.readouterr().out


def test_session_save_in_a_worktree_subdirectory_keys_under_the_parent_repo(
    worktree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subdirectory = worktree / "src" / "pkg"
    subdirectory.mkdir(parents=True)
    transcript = _turns_transcript(tmp_path / "session.jsonl")
    saved = _capture_session_save(monkeypatch)

    pb.session_save(cwd=str(subdirectory), transcript=str(transcript))

    assert len(saved) == 1
    assert saved[0].project == REPO_DIRNAME


def test_session_save_in_the_main_checkout_keys_under_the_repo_name(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transcript = _turns_transcript(tmp_path / "session.jsonl")
    saved = _capture_session_save(monkeypatch)

    pb.session_save(cwd=str(repo), transcript=str(transcript))

    assert len(saved) == 1
    assert saved[0].project == REPO_DIRNAME


def test_session_save_with_no_cwd_resolves_the_current_worktree_directory(
    worktree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Manual invocation passes no --cwd; the getcwd() fallback must go
    through the same identity resolution, not back to the basename."""
    transcript = _turns_transcript(tmp_path / "session.jsonl")
    saved = _capture_session_save(monkeypatch)
    monkeypatch.chdir(worktree)

    pb.session_save(cwd=None, transcript=str(transcript))

    assert len(saved) == 1
    assert saved[0].project == REPO_DIRNAME


def test_session_save_outside_any_repo_keeps_the_basename_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Out of scope by design: repo_identity already falls back to the
    basename, so a non-repo cwd must behave exactly as before."""
    outside = tmp_path / "scratch-dir"
    outside.mkdir()
    transcript = _turns_transcript(tmp_path / "session.jsonl")
    saved = _capture_session_save(monkeypatch)

    pb.session_save(cwd=str(outside), transcript=str(transcript))

    assert len(saved) == 1
    assert saved[0].project == "scratch-dir"


def test_compact_hook_in_a_worktree_saves_and_echoes_the_parent_repo(
    repo: Path,
    worktree: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    transcript = _compact_transcript(tmp_path / "compact.jsonl", "the compacted session")

    saved, _ = _run_compact_hook(monkeypatch, transcript, worktree)

    assert saved == [{"project": REPO_DIRNAME, "summary": "the compacted session"}]
    assert f"[axon] Compact summary salvo: {REPO_DIRNAME}" in capsys.readouterr().err


def test_compact_hook_outside_any_repo_keeps_the_basename_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    outside = tmp_path / "scratch-dir"
    outside.mkdir()
    transcript = _compact_transcript(tmp_path / "compact.jsonl", "compacted outside any repo")

    saved, _ = _run_compact_hook(monkeypatch, transcript, outside)

    assert saved == [{"project": "scratch-dir", "summary": "compacted outside any repo"}]
    assert "[axon] Compact summary salvo: scratch-dir" in capsys.readouterr().err
