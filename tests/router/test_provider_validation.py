from __future__ import annotations

import pytest

from prometheus.router.provider_validation import (
    provider_for_model,
    validate_anthropic_cache_control,
    validate_openrouter_compliance,
)


def test_provider_for_model_maps_known_prefixes() -> None:
    assert provider_for_model("ollama/phi3:mini") == "ollama"
    assert provider_for_model("openrouter/google/gemini") == "openrouter"
    assert provider_for_model("claude-haiku-4-5-20251001") == "anthropic"


def test_validate_anthropic_cache_control_rejects_invalid_type() -> None:
    with pytest.raises(ValueError, match="cache_control.type invalido"):
        validate_anthropic_cache_control(
            [{"role": "system", "content": "x", "cache_control": {"type": "wrong"}}]
        )


def test_validate_openrouter_compliance_requires_fields() -> None:
    with pytest.raises(ValueError, match="compliance incompleto"):
        validate_openrouter_compliance({"zdr": True})
