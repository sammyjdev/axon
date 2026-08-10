"""Embed a lesson through the existing embedder chain.

The engine is injected rather than constructed here. ``engine.py`` already owns
the provider chain (Ollama -> NIM -> DeepInfra); building a second entry point
to it would make two answers to "which provider embedded this vector", and the
lessons corpus is worth nothing if its vectors came from a mix of models.
"""

from __future__ import annotations

from typing import Protocol

from axon.embedder.lesson_text import lesson_text
from axon.models.lesson import LessonCreate


class SupportsEmbedOne(Protocol):
    """The one method of ``EmbedderEngine`` this needs."""

    def embed_one(self, text: str) -> list[float]: ...


def embed_lesson(lesson: LessonCreate, *, engine: SupportsEmbedOne) -> list[float]:
    """Return the embedding of ``lesson``, keyed on its formatted text."""
    return engine.embed_one(
        lesson_text(
            kind=lesson.kind,
            triggers=lesson.triggers,
            mistake=lesson.mistake,
            tell=lesson.tell,
            fix=lesson.fix,
            source=lesson.source,
        )
    )
