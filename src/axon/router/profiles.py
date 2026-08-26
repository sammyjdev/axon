from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Profile(StrEnum):
    BUDGET = "budget"
    # FREE is retained as the alias
    FREE = "free"
    PAID = "paid"


@dataclass(frozen=True)
class ProfileSpec:
    name: str
    description: str
    models: dict[str, str]
    classifier_model: str
    cost_per_1k: dict[str, float]


_BUDGET = ProfileSpec(
    name="budget",
    description="Custo reduzido: DeepInfra + OpenRouter fallback (substitui o antigo 'free')",
    # Model ids verified with real chat completions on 2026-08-26 (see dec-128).
    models={
        "TRIVIAL_COMPLETION": "deepinfra/meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
        "CODE_ANALYSIS": "openrouter/meta-llama/llama-3.3-70b-instruct",
        "ARCHITECTURE": "deepinfra/meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "DEEP_REASONING": "deepinfra/meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "LOCAL_ONLY": "deepinfra/meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
        "UNKNOWN": "deepinfra/meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
    },
    classifier_model="groq/openai/gpt-oss-120b",
    cost_per_1k={
        # cost_per_1k is USD per 1K tokens, one scalar per model, multiplied against
        # PROMPT tokens only (engine.py: `_COST_PER_1K.get(model, 0.0) * approx_tokens/1000`),
        # so it cannot express separate input/output rates. We store max(input, output)
        # per 1K so the budget guard never underestimates.
        # DeepInfra quotes cents/token -> USD per 1K = cents_per_token * 10:
        #   8B:  max(2e-06, 4e-06)   = 4e-06 c/tok   -> 4e-05
        #   70B: max(1e-05, 3.2e-05) = 3.2e-05 c/tok -> 3.2e-04
        # OpenRouter quotes USD/token -> USD per 1K = usd_per_token * 1000:
        #   70b: max(7.1e-07, 7.1e-07) -> 7.1e-04
        # The tier ladder puts the mid rung on OpenRouter so the three rungs stay distinct
        # and the top-tier downgrade crosses providers.
        "deepinfra/meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo": 4e-05,
        "deepinfra/meta-llama/Llama-3.3-70B-Instruct-Turbo": 3.2e-04,
        "openrouter/meta-llama/llama-3.3-70b-instruct": 7.1e-04,
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
    classifier_model="groq/openai/gpt-oss-120b",
    cost_per_1k={
        "openrouter/anthropic/claude-haiku-4": 0.0008,
        "openrouter/anthropic/claude-sonnet-4": 0.009,
        "openrouter/anthropic/claude-opus-4": 0.045,
    },
)


_REGISTRY: dict[str, ProfileSpec] = {
    "budget": _BUDGET,
    "free": _BUDGET,
    "paid": _PAID,
}


def get_profile(name: str | None) -> ProfileSpec:
    key = (name or "budget").strip().lower()
    try:
        return _REGISTRY[key]
    except KeyError as exc:
        raise ValueError(
            f"profile invalido: {name!r}. Disponiveis: {sorted(_REGISTRY)}"
        ) from exc


def available_profiles() -> list[str]:
    return sorted(_REGISTRY)
