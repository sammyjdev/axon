"""Caveman compressor — semantic context compression via configurable Ollama model.

Strips filler words, articles and connectives; keeps method signatures,
business rules and decisions. Returns dense, token-efficient context.
Model is set via PROMETHEUS_CAVEMAN_MODEL (falls back to OLLAMA_MODEL_PRIMARY).
"""
from __future__ import annotations

import logging

import litellm

from prometheus.config.runtime import load_runtime_config

logger = logging.getLogger(__name__)

_RUNTIME = load_runtime_config()

_SHORT_TEXT_WORD_LIMIT = 80

_SYSTEM_PROMPT = (
    "You are a technical context compressor. "
    "Strip all filler: articles, prepositions, politeness, redundant prose. "
    "Keep: method signatures, class names, business rules, invariants, decisions, error codes. "
    "Output only the compressed content, no explanations."
)


async def caveman_compress(text: str, max_tokens: int = 400) -> tuple[str, str | None]:
    """Compress technical context using the configured Ollama model in caveman style.

    Returns (compressed_text, error_note). error_note is None on success.
    Falls back to original text on any failure — never raises.
    """
    if len(text.split()) <= _SHORT_TEXT_WORD_LIMIT:
        return text, None

    model = f"ollama/{_RUNTIME.caveman_model}"
    try:
        response = await litellm.acompletion(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            max_tokens=max_tokens,
            api_base=_RUNTIME.ollama_local_host,
        )
        compressed = response.choices[0].message.content or text
        logger.debug("caveman_compress(%s): %d -> %d chars", model, len(text), len(compressed))
        return compressed, None
    except Exception as exc:  # noqa: BLE001
        logger.warning("caveman_compress fallback (%s): %s", model, exc)
        return text, str(exc)
