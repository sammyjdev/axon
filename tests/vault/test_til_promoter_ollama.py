"""The vault hook talks to the local Ollama server directly.

litellm was the only thing pinning filelock below 4.0, where 13 CVEs are still
open, and the hook only ever called one local Ollama model through it.
"""

from __future__ import annotations

import importlib.metadata
import json

import pytest

from axon.vault import til_promoter


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self._body = json.dumps({"message": {"role": "assistant", "content": content}}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


@pytest.fixture
def ollama(monkeypatch):
    calls: list[dict] = []
    replies: list[str] = []

    def _urlopen(request, timeout):
        calls.append({"url": request.full_url, "body": json.loads(request.data)})
        return _FakeResponse(replies.pop(0))

    monkeypatch.setattr(til_promoter.urllib.request, "urlopen", _urlopen)
    return calls, replies


def test_a_til_is_promoted_when_the_local_model_answers_promote(ollama) -> None:
    calls, replies = ollama
    replies.append("PROMOTE")

    assert til_promoter.should_promote("til body") is True
    assert calls[0]["url"] == "http://localhost:11434/api/chat"
    body = calls[0]["body"]
    assert body["model"] == "gemma4:e4b"
    assert body["stream"] is False
    assert body["messages"][-1] == {"role": "user", "content": "til body"}


def test_a_til_stays_when_the_local_model_answers_keep(ollama) -> None:
    _calls, replies = ollama
    replies.append("KEEP")

    assert til_promoter.should_promote("til body") is False


def test_a_promoted_til_becomes_a_howto_written_by_the_local_model(ollama, tmp_path) -> None:
    _calls, replies = ollama
    replies.append("# HOW-TO: x")
    til = tmp_path / "til-x.md"
    til.write_text("promoted: false\nbody", encoding="utf-8")

    howto = til_promoter.promote_to_howto(til)

    assert howto.read_text() == "# HOW-TO: x"
    assert "promoted: true" in til.read_text()


def test_axon_no_longer_depends_on_litellm() -> None:
    requires = importlib.metadata.requires("axon-context-mcp") or []
    assert not [r for r in requires if r.lower().startswith("litellm")]
