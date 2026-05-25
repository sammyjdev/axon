from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Profile(StrEnum):
    FREE = "free"
    PAID = "paid"


@dataclass(frozen=True)
class ProfileSpec:
    name: str
    description: str
    models: dict[str, str]
    classifier_model: str
    cost_per_1k: dict[str, float]


_FREE = ProfileSpec(
    name="free",
    description="Sem custo: Groq + NVIDIA NIM free tiers (sujeito a rate limits)",
    models={
        "TRIVIAL_COMPLETION": "groq/llama-3.1-8b-instant",
        "CODE_ANALYSIS": "groq/llama-3.3-70b-versatile",
        "ARCHITECTURE": "nvidia_nim/meta/llama-3.1-70b-instruct",
        "DEEP_REASONING": "nvidia_nim/meta/llama-3.1-70b-instruct",
        "LOCAL_ONLY": "groq/llama-3.1-8b-instant",
        "UNKNOWN": "groq/llama-3.1-8b-instant",
    },
    classifier_model="groq/llama-3.1-8b-instant",
    cost_per_1k={
        "groq/llama-3.1-8b-instant": 0.0,
        "groq/llama-3.3-70b-versatile": 0.0,
        "nvidia_nim/meta/llama-3.1-70b-instruct": 0.0,
    },
)


_PAID = ProfileSpec(
    name="paid",
    description="Credito pago: OpenRouter preservando D2 (Claude Haiku/Sonnet/Opus) + Groq pago",
    models={
        "TRIVIAL_COMPLETION": "openrouter/anthropic/claude-haiku-4",
        "CODE_ANALYSIS": "openrouter/anthropic/claude-sonnet-4",
        "ARCHITECTURE": "openrouter/anthropic/claude-opus-4",
        "DEEP_REASONING": "openrouter/anthropic/claude-opus-4",
        "LOCAL_ONLY": "openrouter/anthropic/claude-haiku-4",
        "UNKNOWN": "openrouter/anthropic/claude-haiku-4",
    },
    classifier_model="groq/llama-3.1-8b-instant",
    cost_per_1k={
        "openrouter/anthropic/claude-haiku-4": 0.0008,
        "openrouter/anthropic/claude-sonnet-4": 0.009,
        "openrouter/anthropic/claude-opus-4": 0.045,
        "groq/llama-3.1-8b-instant": 0.00005,
    },
)


_REGISTRY: dict[str, ProfileSpec] = {
    _FREE.name: _FREE,
    _PAID.name: _PAID,
}


def get_profile(name: str | None) -> ProfileSpec:
    key = (name or _FREE.name).strip().lower()
    try:
        return _REGISTRY[key]
    except KeyError as exc:
        raise ValueError(
            f"profile invalido: {name!r}. Disponiveis: {sorted(_REGISTRY)}"
        ) from exc


def available_profiles() -> list[str]:
    return sorted(_REGISTRY)
