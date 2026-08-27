"""session_save must persist a session even when compression fails, and must
compress every turn it was handed - not a fixed tail of the list."""

import json

import pytest

from axon.memory.session_compressor import SessionCompressor


def _turns(n: int) -> list[dict[str, str]]:
    return [{"role": "user", "content": f"turn number {i}"} for i in range(n)]


@pytest.mark.asyncio
async def test_compress_covers_every_turn_added_not_only_the_last_ten(monkeypatch):
    seen = {}

    async def fake_completion(*, model, messages, max_tokens):
        seen["context"] = messages[-1]["content"]

        class _Msg:
            content = "summary"

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        return _Resp()

    monkeypatch.setattr("litellm.acompletion", fake_completion)

    compressor = SessionCompressor()
    for turn in _turns(30):
        compressor.add_turn(turn["role"], turn["content"])
    await compressor.compress()

    assert "turn number 0" in seen["context"], "the oldest turn was dropped"
    assert "turn number 29" in seen["context"]
    # Oldest-first: a summariser handed the session backwards misreads which
    # decision superseded which.
    assert seen["context"].index("turn number 0") < seen["context"].index("turn number 29")


def test_session_is_persisted_with_a_digest_when_compression_fails(tmp_path, monkeypatch):
    # Synchronous on purpose: session_save owns its own asyncio.run().
    """The regression that mattered: a hook with no API key lost the session
    entirely. It must now persist the rule-based digest instead."""
    from axon.cli import pb

    transcript = tmp_path / "s.jsonl"
    transcript.write_text(
        "".join(
            json.dumps({"type": r, "message": {"role": r, "content": c}}) + "\n"
            for r, c in (
                ("user", "why does the session hook never fire?"),
                ("assistant", "looked at src/axon/cli/pb.py"),
                ("user", "fix it"),
            )
        ),
        encoding="utf-8",
    )

    async def boom(self):
        raise RuntimeError("Missing Anthropic API Key")

    monkeypatch.setattr(SessionCompressor, "compress", boom)

    saved = []

    class _Store:
        def __init__(self, *_a, **_kw):
            pass

        async def init(self):
            return None

        async def save_session_memory(self, mem):
            saved.append(mem)
            return 1

    monkeypatch.setattr("axon.store.session_store.SessionStore", _Store)

    pb.session_save(cwd=str(tmp_path), transcript=str(transcript))

    assert len(saved) == 1, "the session was lost when compression failed"
    assert "why does the session hook never fire?" in saved[0].summary
    assert saved[0].raw_turns == 3


@pytest.mark.asyncio
async def test_context_stops_at_the_char_budget_dropping_the_oldest(monkeypatch):
    """The budget is what bounds the context now, so it has to actually bind:
    turns past it are dropped oldest-first, never newest-first."""
    from axon.memory import session_compressor as sc

    seen = {}

    async def fake_completion(*, model, messages, max_tokens):
        seen["context"] = messages[-1]["content"]

        class _R:
            choices = [type("C", (), {"message": type("M", (), {"content": "s"})()})()]

        return _R()

    monkeypatch.setattr("litellm.acompletion", fake_completion)

    compressor = sc.SessionCompressor()
    filler = "x" * sc._MAX_TURN_CHARS
    n = (sc._MAX_CONTEXT_CHARS // sc._MAX_TURN_CHARS) + 10
    for i in range(n):
        compressor.add_turn("user", f"{i:04d}" + filler)
    await compressor.compress()

    assert len(seen["context"]) <= sc._MAX_CONTEXT_CHARS
    assert "0000" not in seen["context"], "oldest turn survived the budget"
    assert f"{n - 1:04d}" in seen["context"], "newest turn was dropped instead"
