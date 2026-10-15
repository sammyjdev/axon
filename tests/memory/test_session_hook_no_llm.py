"""A session is stored as its digest; the end of a session calls no LLM.

Capture carries no LLM by decision. Measured 2026-10-15 on a real transcript:
the LLM summary took 18 s and loaded litellm, and with no key it already fell
back to this digest.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import litellm
import pytest

from axon.memory.digest import digest_turns
from axon.memory.transcript import parse_transcript_turns


def _transcript(path: Path) -> Path:
    path.write_text(
        "".join(
            json.dumps({"type": role, "message": {"role": role, "content": text}}) + "\n"
            for role, text in (
                ("user", "why does the session hook never fire?"),
                ("assistant", "looked at src/axon/cli/pb.py"),
                ("user", "fix it"),
            )
        ),
        encoding="utf-8",
    )
    return path


def test_a_session_is_stored_as_its_digest_without_calling_an_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from axon.cli import pb

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)  # noqa: S603, S607
    transcript = _transcript(tmp_path / "session.jsonl")
    llm_calls: list[dict] = []

    async def recording_completion(**kwargs: object) -> None:
        llm_calls.append(kwargs)
        raise AssertionError("the session hook called an LLM")

    monkeypatch.setattr(litellm, "acompletion", recording_completion)
    saved = []

    class _Store:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def init(self) -> None:
            return None

        async def save_session_memory(self, mem: object) -> int:
            saved.append(mem)
            return 1

        async def drain_pending(self) -> None:
            return None

    monkeypatch.setattr("axon.store.session_store.SessionStore", _Store)

    pb.session_save(cwd=str(tmp_path), transcript=str(transcript))

    assert llm_calls == []
    assert [mem.summary for mem in saved] == [digest_turns(parse_transcript_turns(transcript))]
