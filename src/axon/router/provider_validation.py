from __future__ import annotations


def provider_for_model(model: str) -> str:
    if model.startswith("ollama/"):
        return "ollama"
    if model.startswith("openrouter/"):
        return "openrouter"
    return "anthropic"


def count_tokens_for_provider(provider: str, messages: list[dict]) -> int:
    text = "\n".join(str(msg.get("content", "")) for msg in messages)
    if provider == "openrouter":
        return max(1, len(text) // 4)
    if provider == "anthropic":
        return max(1, len(text) // 4)
    return max(1, len(text) // 4)


def validate_anthropic_cache_control(messages: list[dict]) -> None:
    for msg in messages:
        cache_control = msg.get("cache_control")
        if cache_control is None:
            continue
        if not isinstance(cache_control, dict):
            raise ValueError("cache_control invalido: esperado objeto")
        cache_type = cache_control.get("type")
        if cache_type not in {"ephemeral", "persistent"}:
            raise ValueError("cache_control.type invalido")


def validate_openrouter_compliance(extra: dict | None) -> None:
    if not extra:
        raise ValueError("openrouter requer metadata de compliance")
    required = ("zdr", "retention", "training_use")
    missing = [item for item in required if item not in extra]
    if missing:
        raise ValueError(f"openrouter compliance incompleto: {', '.join(missing)}")
