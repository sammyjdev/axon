"""#187: the hooks hand the payload's session_id to the stored row."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from axon.__main__ import app

runner = CliRunner()


@pytest.fixture
def saved(monkeypatch) -> list:
    rows: list = []

    class _Store:
        def __init__(self, *_args) -> None:
            pass

        async def init(self) -> None:
            return None

        async def save_session_memory(self, mem) -> int:
            rows.append(mem)
            return 1

        async def drain_pending(self) -> None:
            return None

    monkeypatch.setattr("axon.store.session_store.SessionStore", _Store)
    # only the digest step: the other steps need a database
    from axon.hooks import session_end

    monkeypatch.setattr(session_end, "STEPS", [("digest", session_end.store_digest)])
    return rows


@pytest.fixture
def repo_dir(tmp_path, monkeypatch) -> Path:
    # an inherited GIT_DIR would point the hook at another repository
    for name in list(os.environ):
        if name.startswith("GIT_") and name not in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"):
            monkeypatch.delenv(name)
    path = tmp_path / "repo"
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)  # noqa: S607
    return path


def _transcript(tmp_path: Path) -> Path:
    path = tmp_path / "t.jsonl"
    path.write_text(
        "".join(
            json.dumps({"type": r, "message": {"role": r, "content": c}}) + "\n"
            for r, c in (("user", "fix the upsert"), ("assistant", "done"))
        ),
        encoding="utf-8",
    )
    return path


def _compact_transcript(tmp_path: Path) -> Path:
    path = tmp_path / "c.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "user",
                "isCompactSummary": True,
                "message": {"role": "user", "content": [{"type": "text", "text": "summed"}]},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _hook(command: str, transcript: Path, cwd: Path, **extra) -> None:
    payload = {"transcript_path": str(transcript), "cwd": str(cwd), **extra}
    result = runner.invoke(app, [command], input=json.dumps(payload))
    assert result.exit_code == 0, result.output


def test_hook_passes_session_id_to_the_row(saved, repo_dir, tmp_path) -> None:
    _hook("session-hook", _transcript(tmp_path), repo_dir, session_id="abc")

    assert [m.session_id for m in saved] == ["abc"]


def test_compact_hook_passes_session_id(saved, repo_dir, tmp_path) -> None:
    _hook("compact-hook", _compact_transcript(tmp_path), repo_dir, session_id="abc")

    assert [(m.session_id, m.raw_turns) for m in saved] == [("abc", 0)]


@pytest.mark.parametrize("command", ["session-hook", "compact-hook"])
@pytest.mark.parametrize(
    "extra",
    [
        {"session_id": ""},
        {"session_id": "   "},
        {"session_id": 123},
        {"session_id": {}},
        {},
        {"session_id": "a" * 201},
    ],
    ids=["empty", "blank", "number", "object", "absent", "201"],
)
def test_malformed_session_id_saves_without_key(
    saved, repo_dir, tmp_path, command, extra
) -> None:
    transcript = _transcript if command == "session-hook" else _compact_transcript
    _hook(command, transcript(tmp_path), repo_dir, **extra)

    assert [m.session_id for m in saved] == [None]


@pytest.mark.parametrize("fails", [False, True], ids=["returns", "raises"])
@pytest.mark.parametrize("command", ["session-hook", "compact-hook"])
def test_hook_session_id_does_not_leak_to_the_next_save(
    saved, repo_dir, tmp_path, monkeypatch, command, fails
) -> None:
    from axon.store.session_id import current_hook_session_id

    transcript = _transcript if command == "session-hook" else _compact_transcript
    if fails:
        boom = RuntimeError("boom")

        def raise_boom(*_args, **_kwargs):
            raise boom

        if command == "session-hook":
            monkeypatch.setattr("axon.hooks.session_end.run_session_end", raise_boom)
        else:
            monkeypatch.setattr("axon.cli.pb._save_compact_summary", raise_boom)
    payload = {
        "transcript_path": str(transcript(tmp_path)),
        "cwd": str(repo_dir),
        "session_id": "abc",
    }
    runner.invoke(app, [command], input=json.dumps(payload))
    saved.clear()

    result = runner.invoke(
        app,
        ["session", "save", "--cwd", str(repo_dir), "--transcript", str(_transcript(tmp_path))],
    )

    assert result.exit_code == 0, result.output
    assert current_hook_session_id.get() is None
    assert [m.session_id for m in saved] == [None]


def test_manual_save_has_no_session_id(saved, repo_dir, tmp_path) -> None:
    transcript = str(_transcript(tmp_path))
    result = runner.invoke(
        app, ["session", "save", "--cwd", str(repo_dir), "--transcript", transcript]
    )
    assert result.exit_code == 0, result.output
    assert [m.session_id for m in saved] == [None]

    rejected = runner.invoke(app, ["session", "save", "--session-id", "abc"])
    assert rejected.exit_code == 2
