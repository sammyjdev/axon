"""Prompt templates for the LLM decision judge (T5.1)."""

from __future__ import annotations

from axon.core.decision import Decision

_JUDGE_INSTRUCTIONS = (
    "You score an architectural decision on a 0.0-5.0 scale.\n"
    "Criteria: clarity, completeness, alignment with the codebase, and risk\n"
    "awareness. Reply with ONLY the number (e.g. \"3.5\") — no prose."
)


def build_judge_prompt(decision: Decision, context: str = "") -> str:
    """Render the scoring prompt for a Decision, optionally with extra context."""
    files = ", ".join(str(f) for f in decision.files) or "none"
    symbols = ", ".join(decision.symbols) or "none"
    parts = [
        _JUDGE_INSTRUCTIONS,
        "",
        f"Decision id: {decision.id}",
        f"Summary: {decision.summary}",
        f"Repo: {decision.repo}",
        f"Files: {files}",
        f"Symbols: {symbols}",
    ]
    if context:
        parts += ["", "Context:", context]
    return "\n".join(parts)
