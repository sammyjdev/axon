"""Tests for the dec-106-aware ADR model resolver."""

from __future__ import annotations

import pytest

from axon.adr.inference import _adr_model


class TestAdrModelResolution:
    def test_env_override_takes_precedence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AXON_ADR_MODEL", "ollama/phi3:mini")
        assert _adr_model() == "ollama/phi3:mini"

    def test_free_profile_defaults_to_groq(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AXON_ADR_MODEL", raising=False)
        monkeypatch.setenv("AXON_PROVIDER_PROFILE", "free")
        assert _adr_model() == "groq/openai/gpt-oss-120b"

    def test_paid_profile_also_uses_groq_classifier(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Classification is cheap — both profiles share the classifier."""
        monkeypatch.delenv("AXON_ADR_MODEL", raising=False)
        monkeypatch.setenv("AXON_PROVIDER_PROFILE", "paid")
        assert _adr_model() == "groq/openai/gpt-oss-120b"

    def test_env_override_picks_nim(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """User can opt into NIM explicitly even when running FREE profile."""
        monkeypatch.setenv(
            "AXON_ADR_MODEL", "nvidia_nim/meta/llama-3.1-70b-instruct"
        )
        assert _adr_model() == "nvidia_nim/meta/llama-3.1-70b-instruct"
