"""Self-correcting retrieval: grade the retrieval, re-try once, or give up
honestly. Pure orchestration — LLM and graph access are injected callables so
the loop is testable without a live model or the MCP server.

See docs/superpowers/specs/2026-07-01-agentic-retrieval-design.md.
"""
from __future__ import annotations

import re
from collections.abc import Callable

# Calibrated against the golden set (see retrieval_eval). Below LOW: retry
# without asking the judge. At/above HIGH: trust the retrieval. Gray zone in
# between: ask the judge. Similarity is a weak relevance proxy, so the gray zone
# is deliberately wide.
LOW: float = 0.35
HIGH: float = 0.65

_STRUCTURAL_PHRASES = (
    "depende", "dependencia", "quem usa", "quem chama", "quem importa",
    "depends on", "who uses", "who calls", "call graph", "callers of",
    "imported by", "importado", "grafo",
)
# CamelCase (AuthService), dotted access (module.attr), or call syntax (fn()).
_SYMBOL_RE = re.compile(r"[A-Z][a-z]+[A-Z]|\b\w+\.\w+\b|\b\w+\(\)")


def aggregate_score(hits: list[dict]) -> float:
    """Confidence of the retrieval = the best single hit's cosine score."""
    return max((float(h.get("score", 0.0)) for h in hits), default=0.0)


def is_structural(query: str) -> bool:
    """True when the query is about code structure/dependencies, where the
    graph fallback beats vector search."""
    q = query.lower()
    if any(phrase in q for phrase in _STRUCTURAL_PHRASES):
        return True
    return bool(_SYMBOL_RE.search(query))


def grade(
    hits: list[dict],
    query: str,
    code_context: str,
    judge_fn: Callable[[str, str], bool],
) -> tuple[bool, str]:
    """Hybrid cascade. Returns (sufficient, verdict_label). judge_fn is called
    ONLY in the gray zone [LOW, HIGH)."""
    if not hits:
        return False, "empty"
    score = aggregate_score(hits)
    if score < LOW:
        return False, "low_score"
    if score >= HIGH:
        return True, "high_score"
    verdict = judge_fn(query, code_context)
    return (verdict, "judge_sufficient" if verdict else "judge_insufficient")
