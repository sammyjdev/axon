"""Fake-based tests for the bge-m3 provider chain (EMB-2).

NO network calls: every provider here is a plain Python fake so these run
fast and deterministically. Live-network verification is out of scope for
this slice (see .superpowers/sdd/briefs/emb-2-brief.md).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from axon.config.runtime import load_embedder_chain_config
from axon.embedder.engine import EmbedderEngine
from axon.embedder.providers import (
    AllProvidersFailedError,
    check_provider_interchangeable,
    embed_via_chain,
)


def _fake_provider(name: str, call_log: list[str], vector: list[float], *, fails: bool = False):
    def _fn(texts: list[str]) -> list[list[float]]:
        call_log.append(name)
        if fails:
            raise RuntimeError(f"{name} unavailable")
        return [vector for _ in texts]

    return _fn


def test_tries_providers_in_configured_order() -> None:
    """The first provider in the list serves the request; later ones are never called."""
    call_log: list[str] = []
    first = _fake_provider("ollama", call_log, [1.0, 0.0])
    second = _fake_provider("nim", call_log, [0.0, 1.0])

    result = embed_via_chain(["hello"], providers=[first, second])

    assert call_log == ["ollama"]
    assert result == [[1.0, 0.0]]


def test_falls_through_to_next_provider_on_error() -> None:
    """A failing provider is skipped and the next one in order serves the request."""
    call_log: list[str] = []
    broken = _fake_provider("ollama", call_log, [], fails=True)
    healthy = _fake_provider("nim", call_log, [0.0, 1.0])

    result = embed_via_chain(["hello"], providers=[broken, healthy])

    assert call_log == ["ollama", "nim"]
    assert result == [[0.0, 1.0]]


def test_all_providers_fail_raises_clear_error() -> None:
    """When every provider fails, raise instead of silently returning a bad vector."""
    call_log: list[str] = []
    broken_a = _fake_provider("ollama", call_log, [], fails=True)
    broken_b = _fake_provider("nim", call_log, [], fails=True)

    with pytest.raises(AllProvidersFailedError):
        embed_via_chain(["hello"], providers=[broken_a, broken_b])

    assert call_log == ["ollama", "nim"]


def _short_provider(name: str, call_log: list[str], vectors: list[list[float]]):
    """Fake provider that returns HTTP-200-shaped success but fewer vectors than texts."""

    def _fn(texts: list[str]) -> list[list[float]]:
        call_log.append(name)
        return vectors

    return _fn


def test_falls_through_when_provider_returns_fewer_vectors_than_texts() -> None:
    """A malformed 200 response (short/empty vector list) must not be accepted as success --
    it must fall through to the next provider instead of silently dropping chunks."""
    call_log: list[str] = []
    malformed = _short_provider("ollama", call_log, [[1.0, 0.0]])  # 1 vector, 2 texts
    healthy = _fake_provider("nim", call_log, [0.0, 1.0])

    result = embed_via_chain(["hello", "world"], providers=[malformed, healthy])

    assert call_log == ["ollama", "nim"]
    assert result == [[0.0, 1.0], [0.0, 1.0]]


def test_default_chain_is_ollama_then_deepinfra(monkeypatch: pytest.MonkeyPatch) -> None:
    """NIM's bge-m3 embedding endpoint is broken upstream (HTTP 500, verified
    2026-07-21); DeepInfra is the remote provider for bge-m3. NIM stays
    available opt-in by name via AXON_EMBEDDER_CHAIN."""
    monkeypatch.delenv("AXON_EMBEDDER_CHAIN", raising=False)
    chain = load_embedder_chain_config()
    assert [p.name for p in chain.providers] == ["ollama", "deepinfra"]


def test_all_providers_short_raises_clear_error() -> None:
    """When every provider returns a malformed (short) vector list, raise -- never return it."""
    call_log: list[str] = []
    short_a = _short_provider("ollama", call_log, [])
    short_b = _short_provider("nim", call_log, [[1.0, 0.0]])

    with pytest.raises(AllProvidersFailedError):
        embed_via_chain(["hello", "world"], providers=[short_a, short_b])

    assert call_log == ["ollama", "nim"]


def test_returned_vectors_are_l2_normalized() -> None:
    """Vectors are L2-normalized regardless of what the provider returns raw."""
    call_log: list[str] = []
    provider = _fake_provider("ollama", call_log, [3.0, 4.0])  # norm == 5

    result = embed_via_chain(["hello"], providers=[provider])

    [vec] = result
    assert vec == pytest.approx([0.6, 0.8])
    norm = sum(x * x for x in vec) ** 0.5
    assert norm == pytest.approx(1.0)


def test_onboarding_check_passes_on_identical_vectors() -> None:
    """Local and candidate providers returning the same vector are interchangeable."""

    def local(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0]]

    def candidate(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0]]

    assert check_provider_interchangeable(local, candidate) is True


def test_onboarding_check_fails_on_divergent_vectors() -> None:
    """Orthogonal (cos == 0) vectors must fail the >= 0.999 interchangeability gate."""

    def local(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0]]

    def candidate(texts: list[str]) -> list[list[float]]:
        return [[0.0, 1.0]]

    assert check_provider_interchangeable(local, candidate) is False


def test_engine_routes_bge_m3_to_chain() -> None:
    """EmbedderEngine.embed() routes to the chain when model_name == 'bge-m3'."""
    engine = EmbedderEngine(model_name="bge-m3")
    with patch("axon.embedder.engine.embed_via_chain", return_value=[[1.0, 0.0]]) as mock_chain:
        result = engine.embed(["hello"])
    mock_chain.assert_called_once_with(["hello"])
    assert result == [[1.0, 0.0]]


def test_engine_default_model_does_not_use_chain() -> None:
    """The default (non bge-m3) model path is untouched -- chain is never invoked."""
    engine = EmbedderEngine(model_name="BAAI/bge-small-en-v1.5")
    with patch("axon.embedder.engine.embed_via_chain") as mock_chain:
        with patch.object(EmbedderEngine, "_ensure_model") as mock_ensure:
            mock_ensure.return_value.embed.return_value = []
            engine.embed(["hello"])
    mock_chain.assert_not_called()


def test_chain_order_and_membership_are_sourced_from_config(monkeypatch) -> None:
    """A non-default AXON_EMBEDDER_CHAIN order must be reflected exactly -- proves order
    is genuinely read from config, not hardcoded."""
    monkeypatch.setenv("AXON_EMBEDDER_CHAIN", "deepinfra,ollama")

    config = load_embedder_chain_config()

    assert [p.name for p in config.providers] == ["deepinfra", "ollama"]
