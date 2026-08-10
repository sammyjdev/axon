"""Wave B Task 5: embed a lesson through the existing engine chain.

The engine is faked here on purpose. This test is about the wiring - that a
lesson reaches the embedder as its formatted text and comes back at the store's
dimension - not about the quality of a real bge-m3 vector, which no unit test
can assert cheaply.
"""
from __future__ import annotations

from axon.embedder.lesson_embedding import embed_lesson
from axon.models.lesson import LessonCreate
from axon.store.vector_common import VECTOR_SIZE


class _FakeEngine:
    """Stands in for EmbedderEngine, recording what it was asked to embed."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def embed_one(self, text: str) -> list[float]:
        self.seen.append(text)
        return [0.0] * VECTOR_SIZE


def _lesson(**over: object) -> LessonCreate:
    base = {
        "kind": "agent-error",
        "triggers": ["shell-script"],
        "mistake": "used mapfile",
        "tell": "line 3: mapfile: command not found",
        "fix": "read -r -d '' into an array",
        "source": "forge/agent-errors",
    }
    return LessonCreate(**{**base, **over})  # type: ignore[arg-type]


def test_embedding_has_the_stores_dimension():
    vector = embed_lesson(_lesson(), engine=_FakeEngine())
    assert len(vector) == VECTOR_SIZE


def test_embeds_the_formatted_lesson_text_not_a_new_format():
    """The formatter from Task 3 is the single source of the embedding key.

    Asserting the sorted-trigger join catches a second formatter being grown
    here, which would silently split retrieval across two text shapes.
    """
    engine = _FakeEngine()
    embed_lesson(_lesson(triggers=["git", "bash"], tell="TELL-MARKER"), engine=engine)

    assert len(engine.seen) == 1
    text = engine.seen[0]
    assert "TELL-MARKER" in text
    assert "bash; git" in text
