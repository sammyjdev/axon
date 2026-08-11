"""Wave D Task 9: `axon_record_lesson` MCP tool.

Uses a fake in-memory LessonStore, matching the precedent set by
test_outcome_tool.py - the tool unit test does not need Postgres (that is
covered by tests/store/test_lesson_store.py and test_lesson_isolation.py).

The embedder is faked too (Task 6's real bge-m3 semantics are proven
separately, opt-in, via AXON_RUN_LESSON_SEARCH=1), but the fake is not a
no-op: it returns a distinctive, known vector, and the assertions below check
that vector reached the stored record. A tool that stopped calling the
embedder would store a lesson with `embedding=None` - unsearchable - and
these tests catch exactly that, not just that *some* record was saved.
"""

from __future__ import annotations

import pytest

from axon.mcp import server
from axon.models.lesson import LessonRecord
from axon.store.vector_common import VECTOR_SIZE


class _FakeLessonStore:
    def __init__(self) -> None:
        self.saved: list[LessonRecord] = []
        self.inited = False

    async def init(self) -> None:
        self.inited = True

    async def insert(self, lesson: LessonRecord):
        self.saved.append(lesson)
        return lesson.id

    async def get(self, lesson_id):
        for lesson in self.saved:
            if lesson.id == lesson_id:
                return lesson
        return None


class _FakeEngine:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def embed_one(self, text: str) -> list[float]:
        self.calls.append(text)
        vector = [0.0] * VECTOR_SIZE
        vector[0] = 1.0
        return vector


@pytest.fixture
def lesson_store() -> _FakeLessonStore:
    return _FakeLessonStore()


@pytest.fixture
def engine() -> _FakeEngine:
    return _FakeEngine()


@pytest.fixture(autouse=True)
def _use_test_doubles(
    lesson_store: _FakeLessonStore, engine: _FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "_get_lesson_store", lambda: lesson_store)
    monkeypatch.setattr(server, "_get_embedder", lambda: engine)


async def test_tool_is_registered() -> None:
    tools = await server.mcp.list_tools()
    names = {t.name for t in tools}
    assert "axon_record_lesson" in names


async def test_record_lesson_persists_a_retrievable_record(
    lesson_store: _FakeLessonStore, engine: _FakeEngine
) -> None:
    out = await server.axon_record_lesson(
        kind="agent-error",
        triggers=["shell-script", "bash"],
        mistake="used mapfile, which bash 3.2 does not have",
        tell="line 3: mapfile: command not found",
        fix="read -r -d '' into an array",
        source="forge/agent-errors",
    )

    assert lesson_store.inited
    assert len(lesson_store.saved) == 1
    saved = lesson_store.saved[0]
    assert str(saved.id) in out
    assert saved.kind == "agent-error"
    assert saved.triggers == ["shell-script", "bash"]
    assert saved.mistake == "used mapfile, which bash 3.2 does not have"

    retrieved = await lesson_store.get(saved.id)
    assert retrieved is not None


async def test_record_lesson_stores_the_embedding_not_a_null_vector(
    lesson_store: _FakeLessonStore, engine: _FakeEngine
) -> None:
    """A record with no vector is unsearchable and must not pass as success."""
    await server.axon_record_lesson(
        kind="craft-lesson",
        triggers=["imaplib"],
        mistake="assumed select() raises on a missing mailbox",
        tell="silently drops back to AUTH state",
        fix="check the typ returned explicitly",
        source="merit/mail.py",
    )

    assert engine.calls, "the tool must call the embedder, not skip it"
    saved = lesson_store.saved[0]
    assert saved.embedding is not None
    assert saved.embedding[0] == 1.0
