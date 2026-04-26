from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from prometheus.router.compressor import caveman_compress

_LONG_TEXT = " ".join(["word"] * 100)
_SHORT_TEXT = " ".join(["word"] * 40)


@pytest.mark.asyncio
async def test_compresses_long_text() -> None:
    fake_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="compressed result"))]
    )
    with patch("prometheus.router.compressor.litellm.acompletion", new=AsyncMock(return_value=fake_response)) as mock_llm:
        result, error = await caveman_compress(_LONG_TEXT, max_tokens=400)

    assert result == "compressed result"
    assert error is None
    mock_llm.assert_awaited_once()


@pytest.mark.asyncio
async def test_skips_short_text() -> None:
    with patch("prometheus.router.compressor.litellm.acompletion", new=AsyncMock()) as mock_llm:
        result, error = await caveman_compress(_SHORT_TEXT)

    assert result == _SHORT_TEXT
    assert error is None
    mock_llm.assert_not_awaited()


@pytest.mark.asyncio
async def test_fallback_on_error() -> None:
    with patch(
        "prometheus.router.compressor.litellm.acompletion",
        new=AsyncMock(side_effect=RuntimeError("ollama unavailable")),
    ):
        result, error = await caveman_compress(_LONG_TEXT)

    assert result == _LONG_TEXT
    assert error == "ollama unavailable"
