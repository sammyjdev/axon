"""The session compressor must use the model the router already resolves for
the compressor role. Hardcoding an Anthropic id made session capture depend on
an API key this machine does not have and does not need - the subscription and
the already-configured providers were right there.
"""

import pytest

from axon.memory.session_compressor import SessionCompressor


@pytest.mark.asyncio
async def test_compressor_uses_the_routed_model_not_a_hardcoded_anthropic_id(monkeypatch):
    seen = {}

    async def fake_completion(**kwargs):
        seen.update(kwargs)

        class _R:
            choices = [type("C", (), {"message": type("M", (), {"content": "s"})()})()]

        return _R()

    monkeypatch.setattr("litellm.acompletion", fake_completion)
    monkeypatch.setenv("AXON_SESSION_COMPRESSOR_MODEL", "groq/openai/gpt-oss-120b")

    compressor = SessionCompressor()
    compressor.add_turn("user", "a")
    compressor.add_turn("assistant", "b")
    await compressor.compress()

    assert seen["model"] == "groq/openai/gpt-oss-120b"
    assert "anthropic" not in seen["model"]


@pytest.mark.asyncio
async def test_an_ollama_model_gets_its_api_base_so_a_local_run_reaches_the_host(monkeypatch):
    seen = {}

    async def fake_completion(**kwargs):
        seen.update(kwargs)

        class _R:
            choices = [type("C", (), {"message": type("M", (), {"content": "s"})()})()]

        return _R()

    monkeypatch.setattr("litellm.acompletion", fake_completion)
    monkeypatch.setenv("AXON_SESSION_COMPRESSOR_MODEL", "phi3:mini")

    compressor = SessionCompressor()
    compressor.add_turn("user", "a")
    await compressor.compress()

    assert seen["model"] == "ollama/phi3:mini", "a bare name must resolve to the ollama provider"
    assert seen["api_base"], "an ollama call with no api_base never reaches the host"
