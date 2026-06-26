"""Caveman compressor — semantic context compression via configurable Ollama model.

Strips filler words, articles and connectives; keeps method signatures,
business rules and decisions. Returns dense, token-efficient context.
Model is set via AXON_CAVEMAN_MODEL (falls back to OLLAMA_MODEL_PRIMARY).
"""

from __future__ import annotations

import logging

import litellm

from axon.config.runtime import is_corporate_context, load_runtime_config
from axon.context.compression_quality import (
    assess_compression_confidence,
    compression_confidence_fallback_note,
    compression_quality_note,
    extract_required_symbols,
)
from axon.router.llm_backend import litellm_kwargs, resolve_litellm_model

logger = logging.getLogger(__name__)

_RUNTIME = load_runtime_config()

_SHORT_TEXT_WORD_LIMIT = 80

_SYSTEM_PROMPT = (
    "You are a technical context compressor. "
    "Strip all filler: articles, prepositions, politeness, redundant prose. "
    "Keep: method signatures, class names, business rules, invariants, decisions, error codes. "
    "Output only the compressed content, no explanations."
)

_STRICT_SYSTEM_PROMPT = (
    "You are a lossless technical context compressor. "
    "Compress only by removing whitespace, prose filler, duplicated boilerplate, "
    "and non-essential text. "
    "Preserve every required symbol exactly as written. "
    "Preserve each retrieved source block enough to keep its purpose, calls, "
    "error handling, and return value. "
    "Never include instructions, examples, markdown task text, or explanations. "
    "Output only compressed context."
)


async def caveman_compress(
    text: str,
    max_tokens: int = 400,
    *,
    required_symbols: list[str] | None = None,
    strict: bool = False,
    ctx: str | None = None,
) -> tuple[str, str | None]:
    """Compress technical context using the configured model in caveman style.

    Returns (compressed_text, error_note). error_note is None on success.
    Falls back to original text on any failure — never raises. Corporate context
    (dec-122) is never sent to a hosted provider; it falls back to the original.
    """
    if len(text.split()) <= _SHORT_TEXT_WORD_LIMIT:
        return text, None

    model = resolve_litellm_model(_RUNTIME.caveman_model)
    if is_corporate_context(ctx) and not model.startswith("ollama/"):
        return text, "corporate context: cloud compression blocked"
    backend = litellm_kwargs(
        model, ollama_host=_RUNTIME.ollama_local_host, num_ctx=_RUNTIME.caveman_num_ctx
    )
    system_prompt = _STRICT_SYSTEM_PROMPT if strict else _SYSTEM_PROMPT
    user_content = text
    if strict and required_symbols:
        user_content = (
            f"Required symbols to preserve exactly: {', '.join(required_symbols)}\n\n{text}"
        )
    try:
        response = await litellm.acompletion(
            **backend,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            max_tokens=max_tokens,
        )
        compressed = response.choices[0].message.content or text
        logger.debug("caveman_compress(%s): %d -> %d chars", model, len(text), len(compressed))
        return compressed, None
    except Exception as exc:  # noqa: BLE001
        logger.warning("caveman_compress fallback (%s): %s", model, exc)
        return text, str(exc)


async def caveman_compress_guarded(
    text: str, max_tokens: int, *, ctx: str | None = None
) -> tuple[str, str | None]:
    """Compress with quality guard and strict retry before falling back to original text."""
    required_symbols = extract_required_symbols(text)
    caveman_out, caveman_note = await caveman_compress(
        text,
        max_tokens=max_tokens,
        required_symbols=required_symbols,
        ctx=ctx,
    )
    caveman_quality_note = compression_quality_note(text, caveman_out)
    caveman_confidence = assess_compression_confidence(text, caveman_out)
    if not caveman_quality_note and not caveman_confidence.fallback_to_full_context:
        return caveman_out, caveman_note

    retry_out, retry_note = await caveman_compress(
        text,
        max_tokens=max_tokens,
        required_symbols=required_symbols,
        strict=True,
        ctx=ctx,
    )
    retry_quality_note = compression_quality_note(text, retry_out)
    retry_confidence = assess_compression_confidence(text, retry_out)
    if (
        retry_note is None
        and retry_quality_note is None
        and not retry_confidence.fallback_to_full_context
    ):
        return retry_out, None

    return text, (
        retry_note
        or retry_quality_note
        or compression_confidence_fallback_note(retry_confidence)
        or caveman_note
        or caveman_quality_note
        or compression_confidence_fallback_note(caveman_confidence)
    )
