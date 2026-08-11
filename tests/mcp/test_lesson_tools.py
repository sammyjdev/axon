"""Wave D Task 9 + 10: `axon_record_lesson` and `axon_search_lessons` MCP tools.

Uses a fake in-memory LessonStore, matching the precedent set by
test_outcome_tool.py - the tool unit test does not need Postgres (that is
covered by tests/store/test_lesson_store.py and test_lesson_isolation.py).

The embedder is faked too (Task 6's real bge-m3 semantics are proven
separately, opt-in, via AXON_RUN_LESSON_SEARCH=1), but the fake is not a
no-op: it returns a distinctive, known vector, and the assertions below check
that vector reached the stored record. A tool that stopped calling the
embedder would store a lesson with `embedding=None` - unsearchable - and
these tests catch exactly that, not just that *some* record was saved.

For search, the paraphrase claim itself is already proven with real bge-m3
semantics by tests/store/test_lesson_search.py::
test_paraphrased_query_ranks_the_paraphrased_lesson_first (opt-in via
AXON_RUN_LESSON_SEARCH=1). Re-proving paraphrase here with a fake engine would
be decorative: a hand-picked vector table can be made to rank anything first,
which would look like a paraphrase test while proving nothing about semantics.
What is NOT proven anywhere else is that the tool actually calls
LessonStore.search with the query/kind/triggers/limit it was given, embeds the
query rather than skipping it, and surfaces the store's result order and
content (not just "some" record) - that is the wiring covered below, with a
fake LessonStore.search that records its call and returns a canned,
order-distinguishable result list.
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
        self.search_calls: list[dict] = []
        self.search_results: list[LessonRecord] = []

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

    async def search(self, query, *, engine, kind=None, triggers=None, limit=5):
        # Calls engine.embed_one itself, matching the real LessonStore.search
        # (the embedding happens inside the store, not the tool) - so a tool
        # that passed a broken/unused engine through would still be caught.
        # Otherwise just records the call: the tests assert the tool is a
        # thin, faithful wrapper, not that search itself ranks correctly
        # (Task 6 owns that, against real Postgres).
        engine.embed_one(query)
        self.search_calls.append(
            {"query": query, "kind": kind, "triggers": triggers, "limit": limit}
        )
        return self.search_results


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


def _lesson_record(**over: object) -> LessonRecord:
    base = {
        "kind": "agent-error",
        "triggers": ["bash"],
        "mistake": "m",
        "tell": "t",
        "fix": "f",
        "source": "s",
    }
    return LessonRecord(**{**base, **over})  # type: ignore[arg-type]


async def test_search_lessons_tool_is_registered() -> None:
    tools = await server.mcp.list_tools()
    names = {t.name for t in tools}
    assert "axon_search_lessons" in names


async def test_search_lessons_embeds_the_query_and_preserves_the_stores_order(
    lesson_store: _FakeLessonStore, engine: _FakeEngine
) -> None:
    """The store, not the tool, decides ranking (Task 6) - the tool must not
    re-sort, drop, or truncate what the store returns.
    """
    near = _lesson_record(mistake="near lesson")
    far = _lesson_record(mistake="far lesson")
    lesson_store.search_results = [near, far]

    out = await server.axon_search_lessons(query="a paraphrase of the near lesson")

    assert engine.calls == ["a paraphrase of the near lesson"], (
        "the tool must embed the query, not skip it"
    )
    assert out.index(str(near.id)) < out.index(str(far.id))
    assert "near lesson" in out
    assert "far lesson" in out


async def test_search_lessons_passes_kind_triggers_and_limit_through(
    lesson_store: _FakeLessonStore, engine: _FakeEngine
) -> None:
    await server.axon_search_lessons(
        query="q", kind="craft-lesson", triggers=["imaplib"], limit=2
    )

    assert lesson_store.search_calls == [
        {"query": "q", "kind": "craft-lesson", "triggers": ["imaplib"], "limit": 2}
    ]


async def test_search_lessons_with_no_hits_says_so(
    lesson_store: _FakeLessonStore, engine: _FakeEngine
) -> None:
    lesson_store.search_results = []

    out = await server.axon_search_lessons(query="nothing matches this")

    assert "no lesson" in out.lower()
