"""Format a lesson into deterministic text used as an embedding key.

The retrieval key is the shape of the task (``kind``, ``triggers``,
``mistake``) plus how the mistake showed up (``tell``). The ``fix`` is
payload: it is included so distinct fixes yield distinct strings, but it
sits last so it does not dominate the embedding signal.
"""

from __future__ import annotations


def lesson_text(
    *,
    kind: str,
    triggers: list[str],
    mistake: str,
    tell: str,
    fix: str,
    source: str | None = None,
) -> str:
    """Render a lesson as deterministic text for embedding.

    Triggers are sorted so the set of signals - not the call order - shapes
    the embedding. ``fix`` is appended last as payload; ``source`` is only
    included when provided.
    """
    ordered_triggers = sorted(triggers)
    lines = [
        f"{kind}: {mistake}",
        f"triggers: {'; '.join(ordered_triggers)}",
        f"tell: {tell}",
        f"fix: {fix}",
    ]
    if source is not None:
        lines.append(f"source: {source}")
    return "\n".join(lines)
