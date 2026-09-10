"""Gap coverage for #186: behaviors the other session-memory key suites miss.

- compact_hook resolves a worktree *subdirectory* to the parent repo (a
  `--show-toplevel` basename would still yield the worktree name, and a plain
  basename would yield the leaf directory).
- compact_hook with no `cwd` in the payload routes the getcwd() fallback
  through repo_identity too.
- Both commands resolve the identity of the cwd they were GIVEN, not of the
  process cwd (the hook runner's cwd is unrelated to the session's).
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

REPO_DIRNAME = "gap-key-repo"
WORKTREE_DIRNAME = "gap-worktree-186"
OTHER_REPO_DIRNAME = "unrelated-process-repo"


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(  # noqa: S603
        ["git", *args], cwd=cwd, check=True, capture_output=True  # noqa: S603, S607
    )


def _init_repo(repo_dir: Path) -> Path:
    repo_dir.mkdir()
    _git(["init", "-b", "main"], repo_dir)
    _git(["config", "user.email", "test@axon.dev"], repo_dir)
    _git(["config", "user.name", "AXON Test"], repo_dir)
    (repo_dir / "README.md").write_text("seed\n", encoding="utf-8")
    _git(["add", "."], repo_dir)
    _git(["commit", "-m", "test: seed"], repo_dir)
    return repo_dir


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    repo_dir = _init_repo(tmp_path / REPO_DIRNAME)
    worktree_dir = tmp_path / WORKTREE_DIRNAME
    _git(["worktree", "add", "-b", "issue-186", str(worktree_dir)], repo_dir)
    return worktree_dir


@pytest.fixture
def other_repo(tmp_path: Path) -> Path:
    return _init_repo(tmp_path / OTHER_REPO_DIRNAME)


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


def _run_compact_hook(monkeypatch: pytest.MonkeyPatch, payload: dict) -> list:
    saved: list = []
    monkeypatch.setattr(pb, "_save_compact_summary", lambda **kw: saved.append(kw))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    pb.compact_hook()
    return saved


def _capture_session_save(monkeypatch: pytest.MonkeyPatch) -> list:
    saved: list = []

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


def test_compact_hook_in_a_worktree_subdirectory_keys_under_the_parent_repo(
    worktree: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    subdirectory = worktree / "src" / "pkg"
    subdirectory.mkdir(parents=True)
    transcript = _compact_transcript(tmp_path / "compact.jsonl", "sub summary")

    saved = _run_compact_hook(
        monkeypatch, {"transcript_path": str(transcript), "cwd": str(subdirectory)}
    )

    assert saved == [{"project": REPO_DIRNAME, "summary": "sub summary"}]
    assert f"[axon] Compact summary salvo: {REPO_DIRNAME}" in capsys.readouterr().err


def test_compact_hook_without_cwd_in_payload_resolves_the_process_worktree(
    worktree: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    transcript = _compact_transcript(tmp_path / "compact.jsonl", "no cwd summary")
    monkeypatch.chdir(worktree)

    saved = _run_compact_hook(monkeypatch, {"transcript_path": str(transcript)})

    assert saved == [{"project": REPO_DIRNAME, "summary": "no cwd summary"}]
    assert f"[axon] Compact summary salvo: {REPO_DIRNAME}" in capsys.readouterr().err


def test_compact_hook_resolves_the_payload_cwd_not_the_process_cwd(
    worktree: Path,
    other_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    transcript = _compact_transcript(tmp_path / "compact.jsonl", "payload summary")
    monkeypatch.chdir(other_repo)

    saved = _run_compact_hook(
        monkeypatch, {"transcript_path": str(transcript), "cwd": str(worktree)}
    )

    assert saved == [{"project": REPO_DIRNAME, "summary": "payload summary"}]
    err = capsys.readouterr().err
    assert f"[axon] Compact summary salvo: {REPO_DIRNAME}" in err
    assert OTHER_REPO_DIRNAME not in err


def test_session_save_resolves_the_given_cwd_not_the_process_cwd(
    worktree: Path,
    other_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        "".join(
            json.dumps({"type": role, "message": {"role": role, "content": content}})
            + "\n"
            for role, content in (("user", "question"), ("assistant", "answer"))
        ),
        encoding="utf-8",
    )
    saved = _capture_session_save(monkeypatch)
    monkeypatch.chdir(other_repo)

    pb.session_save(cwd=str(worktree), transcript=str(transcript))

    assert len(saved) == 1
    assert saved[0].project == REPO_DIRNAME
    assert f"Session memory salva: {REPO_DIRNAME}" in capsys.readouterr().out
