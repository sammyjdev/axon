from __future__ import annotations

import re
from dataclasses import dataclass

_CONTAMINATION_MARKERS = (
    "## your task",
    "your task:",
    "input (original code)",
    "output (compressed code)",
    "compress the provided",
    "provided code snippet",
)

_CLI_SYMBOL_RE = re.compile(r"^\[[0-9.]+\]\s+.+?\s+::\s+([^:\n]+?)\s+::", re.MULTILINE)
_MCP_SYMBOL_RE = re.compile(r"^###\s+(.+?)\s+\(", re.MULTILINE)
_LOW_CONFIDENCE_THRESHOLD = 0.6
_OVERCOMPRESSION_RATIO = 0.05
_LONG_SOURCE_WORD_LIMIT = 80


@dataclass(frozen=True)
class CompressionConfidence:
    score: float
    reasons: tuple[str, ...]
    fallback_to_full_context: bool


def compression_contamination_note(text: str) -> str | None:
    lowered = text.lower()
    for marker in _CONTAMINATION_MARKERS:
        if marker in lowered:
            return f"compression output rejected: prompt contamination marker '{marker}'"
    return None


def compression_preservation_note(source_text: str, compressed_text: str) -> str | None:
    missing = missing_required_symbols(source_text, compressed_text)
    if missing:
        sample = ", ".join(missing[:3])
        return f"compression output rejected: missing source symbol(s): {sample}"
    return None


def compression_quality_note(source_text: str, compressed_text: str) -> str | None:
    return compression_contamination_note(compressed_text) or compression_preservation_note(
        source_text,
        compressed_text,
    )


def assess_compression_confidence(source_text: str, compressed_text: str) -> CompressionConfidence:
    reasons: list[str] = []
    score = 1.0

    if not compressed_text.strip():
        reasons.append("empty_output")
        return CompressionConfidence(score=0.0, reasons=tuple(reasons), fallback_to_full_context=True)

    if compression_contamination_note(compressed_text):
        reasons.append("prompt_contamination")
        return CompressionConfidence(score=0.0, reasons=tuple(reasons), fallback_to_full_context=True)

    missing = missing_required_symbols(source_text, compressed_text)
    if missing:
        reasons.append("missing_required_symbols")
        score -= min(0.8, 0.25 * len(missing))

    source_words = len(source_text.split())
    compressed_words = len(compressed_text.split())
    compression_ratio = compressed_words / source_words if source_words else 1.0
    if (
        source_words >= _LONG_SOURCE_WORD_LIMIT
        and compression_ratio <= _OVERCOMPRESSION_RATIO
        and not extract_required_symbols(source_text)
    ):
        reasons.append("overcompressed_without_anchors")
        score -= 0.5

    score = max(0.0, round(score, 2))
    return CompressionConfidence(
        score=score,
        reasons=tuple(reasons),
        fallback_to_full_context=score < _LOW_CONFIDENCE_THRESHOLD,
    )


def compression_confidence_fallback_note(confidence: CompressionConfidence) -> str | None:
    if not confidence.fallback_to_full_context:
        return None
    if confidence.reasons:
        return f"compression confidence too low: {', '.join(confidence.reasons)}"
    return "compression confidence too low"


def extract_required_symbols(text: str) -> list[str]:
    symbols: list[str] = []
    for match in [*_CLI_SYMBOL_RE.finditer(text), *_MCP_SYMBOL_RE.finditer(text)]:
        symbol = match.group(1).strip()
        if symbol and symbol != "<sem símbolo>" and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def missing_required_symbols(source_text: str, compressed_text: str) -> list[str]:
    symbols = extract_required_symbols(source_text)
    if not symbols:
        return []
    return [symbol for symbol in symbols if symbol not in compressed_text]
