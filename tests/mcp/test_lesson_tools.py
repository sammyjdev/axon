"""`axon_record_lesson` and `axon_search_lessons` MCP tools.

Uses a fake in-memory LessonStore, matching test_outcome_tool.py: the tool unit test
does not need Postgres (ranking is covered by tests/store/test_lesson_search.py). What
is proven here is the wiring: the tool hands the store the query, kind, triggers and
limit it was given, and surfaces the store's result order and content.
"""

from __future__ import annotations

import pytest

from axon.mcp import server
from axon.models.lesson import LessonRecord


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

    async def search(self, query, *, kind=None, triggers=None, limit=5):
        self.search_calls.append(
            {"query": query, "kind": kind, "triggers": triggers, "limit": limit}
        )
        return self.search_results


@pytest.fixture
def lesson_store() -> _FakeLessonStore:
    return _FakeLessonStore()


@pytest.fixture(autouse=True)
def _use_test_doubles(lesson_store: _FakeLessonStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "_get_lesson_store", lambda: lesson_store)


async def test_tool_is_registered() -> None:
    tools = await server.mcp.list_tools()
    names = {t.name for t in tools}
    assert "axon_record_lesson" in names


async def test_record_lesson_persists_a_retrievable_record(
    lesson_store: _FakeLessonStore,
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


async def test_search_lessons_preserves_the_stores_order(
    lesson_store: _FakeLessonStore,
) -> None:
    """The store decides ranking; the tool must not re-sort, drop or truncate it."""
    near = _lesson_record(mistake="near lesson")
    far = _lesson_record(mistake="far lesson")
    lesson_store.search_results = [near, far]

    out = await server.axon_search_lessons(query="the near lesson")

    assert lesson_store.search_calls[0]["query"] == "the near lesson"
    assert out.index(str(near.id)) < out.index(str(far.id))
    assert "near lesson" in out
    assert "far lesson" in out


async def test_search_lessons_passes_kind_triggers_and_limit_through(
    lesson_store: _FakeLessonStore,
) -> None:
    await server.axon_search_lessons(
        query="q", kind="craft-lesson", triggers=["imaplib"], limit=2
    )

    assert lesson_store.search_calls == [
        {"query": "q", "kind": "craft-lesson", "triggers": ["imaplib"], "limit": 2}
    ]


async def test_search_lessons_with_no_hits_says_so(
    lesson_store: _FakeLessonStore,
) -> None:
    lesson_store.search_results = []

    out = await server.axon_search_lessons(query="nothing matches this")

    assert "no lesson" in out.lower()
