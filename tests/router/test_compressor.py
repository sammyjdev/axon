from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from axon.router.compressor import caveman_compress, caveman_compress_guarded

_LONG_TEXT = " ".join(["word"] * 100)
_SHORT_TEXT = " ".join(["word"] * 40)


@pytest.mark.asyncio
async def test_compresses_long_text(monkeypatch) -> None:
    # Pin the ollama local role so the num_ctx path is exercised deterministically,
    # independent of the dec-122 hosted-default flag / .env.local.
    monkeypatch.setattr(
        "axon.router.compressor._RUNTIME",
        SimpleNamespace(
            caveman_model="phi3:mini",
            ollama_local_host="http://desktop:11434",
            caveman_num_ctx=4096,
        ),
    )
    fake_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="compressed result"))]
    )
    with patch(
        "axon.router.compressor.litellm.acompletion",
        new=AsyncMock(return_value=fake_response),
    ) as mock_llm:
        result, error = await caveman_compress(_LONG_TEXT, max_tokens=400)

    assert result == "compressed result"
    assert error is None
    mock_llm.assert_awaited_once()
    assert mock_llm.await_args.kwargs["extra_body"] == {"options": {"num_ctx": 4096}}


@pytest.mark.asyncio
async def test_strict_compression_includes_required_symbols(monkeypatch) -> None:
    # Pin the ollama local role so the num_ctx path is exercised deterministically.
    monkeypatch.setattr(
        "axon.router.compressor._RUNTIME",
        SimpleNamespace(
            caveman_model="phi3:mini",
            ollama_local_host="http://desktop:11434",
            caveman_num_ctx=4096,
        ),
    )
    fake_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="index_path preserved"))]
    )
    with patch(
        "axon.router.compressor.litellm.acompletion",
        new=AsyncMock(return_value=fake_response),
    ) as mock_llm:
        result, error = await caveman_compress(
            _LONG_TEXT,
            max_tokens=400,
            required_symbols=["index_path"],
            strict=True,
        )

    assert result == "index_path preserved"
    assert error is None
    messages = mock_llm.await_args.kwargs["messages"]
    assert "lossless technical context compressor" in messages[0]["content"]
    assert "Required symbols to preserve exactly: index_path" in messages[1]["content"]
    assert mock_llm.await_args.kwargs["extra_body"] == {"options": {"num_ctx": 4096}}


@pytest.mark.asyncio
async def test_corporate_ctx_blocks_cloud_compression(monkeypatch) -> None:
    monkeypatch.setattr(
        "axon.router.compressor._RUNTIME",
        SimpleNamespace(
            caveman_model="groq/openai/gpt-oss-120b",
            ollama_local_host="http://desktop:11434",
            caveman_num_ctx=4096,
        ),
    )
    with patch("axon.router.compressor.litellm.acompletion", new=AsyncMock()) as mock_llm:
        result, note = await caveman_compress(_LONG_TEXT, max_tokens=400, ctx="work")

    mock_llm.assert_not_awaited()  # corporate content never leaves for a hosted provider
    assert result == _LONG_TEXT
    assert note is not None


@pytest.mark.asyncio
async def test_corporate_ctx_allows_local_compression(monkeypatch) -> None:
    monkeypatch.setattr(
        "axon.router.compressor._RUNTIME",
        SimpleNamespace(
            caveman_model="phi3:mini",
            ollama_local_host="http://desktop:11434",
            caveman_num_ctx=4096,
        ),
    )
    fake = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="local out"))])
    with patch(
        "axon.router.compressor.litellm.acompletion", new=AsyncMock(return_value=fake)
    ) as mock_llm:
        result, note = await caveman_compress(_LONG_TEXT, max_tokens=400, ctx="work")

    mock_llm.assert_awaited_once()  # local model is fine for corporate ctx
    assert result == "local out"


@pytest.mark.asyncio
async def test_guarded_compression_retries_with_strict_symbol_preservation() -> None:
    source = "\n".join(
        [
            "[0.9] /tmp/a.py :: index_path :: async def index_path(): ...",
            "[0.8] /tmp/b.py :: _semantic_search_hits :: async def _semantic_search_hits(): ...",
        ]
    )
    calls: list[dict[str, object]] = []

    async def fake_caveman(_text, max_tokens, *, required_symbols=None, strict=False, ctx=None):
        _ = max_tokens
        calls.append({"required_symbols": required_symbols, "strict": strict})
        if strict:
            return "index_path + _semantic_search_hits compressed safely", None
        return "_semantic_search_hits only", None

    with patch("axon.router.compressor.caveman_compress", new=fake_caveman):
        result, error = await caveman_compress_guarded(source, max_tokens=400)

    assert result == "index_path + _semantic_search_hits compressed safely"
    assert error is None
    assert calls == [
        {"required_symbols": ["index_path", "_semantic_search_hits"], "strict": False},
        {"required_symbols": ["index_path", "_semantic_search_hits"], "strict": True},
    ]


@pytest.mark.asyncio
async def test_guarded_compression_falls_back_when_retry_fails_quality() -> None:
    source = "\n".join(
        [
            "[0.9] /tmp/a.py :: index_path :: async def index_path(): ...",
            "[0.8] /tmp/b.py :: _semantic_search_hits :: async def _semantic_search_hits(): ...",
        ]
    )

    async def fake_caveman(_text, max_tokens, *, required_symbols=None, strict=False, ctx=None):
        _ = (max_tokens, required_symbols)
        if strict:
            return "## Your task: compress _semantic_search_hits", None
        return "_semantic_search_hits only", None

    with patch("axon.router.compressor.caveman_compress", new=fake_caveman):
        result, error = await caveman_compress_guarded(source, max_tokens=400)

    assert result == source
    assert error is not None
    assert "prompt contamination" in error


@pytest.mark.asyncio
async def test_guarded_compression_falls_back_when_confidence_is_too_low() -> None:
    source = " ".join(f"token-{idx}" for idx in range(120))

    async def fake_caveman(_text, max_tokens, *, required_symbols=None, strict=False, ctx=None):
        _ = (max_tokens, required_symbols, strict)
        return "brief summary only", None

    with patch("axon.router.compressor.caveman_compress", new=fake_caveman):
        result, error = await caveman_compress_guarded(source, max_tokens=400)

    assert result == source
    assert error == "compression confidence too low: overcompressed_without_anchors"


@pytest.mark.asyncio
async def test_skips_short_text() -> None:
    with patch("axon.router.compressor.litellm.acompletion", new=AsyncMock()) as mock_llm:
        result, error = await caveman_compress(_SHORT_TEXT)

    assert result == _SHORT_TEXT
    assert error is None
    mock_llm.assert_not_awaited()


@pytest.mark.asyncio
async def test_fallback_on_error() -> None:
    with patch(
        "axon.router.compressor.litellm.acompletion",
        new=AsyncMock(side_effect=RuntimeError("ollama unavailable")),
    ):
        result, error = await caveman_compress(_LONG_TEXT)

    assert result == _LONG_TEXT
    assert error == "ollama unavailable"
