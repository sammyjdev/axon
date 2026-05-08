from __future__ import annotations

import re

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


def compression_contamination_note(text: str) -> str | None:
    lowered = text.lower()
    for marker in _CONTAMINATION_MARKERS:
        if marker in lowered:
            return f"compression output rejected: prompt contamination marker '{marker}'"
    return None


def compression_preservation_note(source_text: str, compressed_text: str) -> str | None:
    symbols = extract_required_symbols(source_text)
    if not symbols:
        return None

    missing = [symbol for symbol in symbols if symbol not in compressed_text]
    if missing:
        sample = ", ".join(missing[:3])
        return f"compression output rejected: missing source symbol(s): {sample}"
    return None


def compression_quality_note(source_text: str, compressed_text: str) -> str | None:
    return compression_contamination_note(compressed_text) or compression_preservation_note(
        source_text,
        compressed_text,
    )


def extract_required_symbols(text: str) -> list[str]:
    symbols: list[str] = []
    for match in [*_CLI_SYMBOL_RE.finditer(text), *_MCP_SYMBOL_RE.finditer(text)]:
        symbol = match.group(1).strip()
        if symbol and symbol != "<sem símbolo>" and symbol not in symbols:
            symbols.append(symbol)
    return symbols
