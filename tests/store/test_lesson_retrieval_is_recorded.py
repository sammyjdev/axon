"""A lesson that is never retrieved and one that is retrieved daily look identical.

The `lessons` table records what was captured - id, kind, triggers, mistake, tell, fix,
source, created_at, vector - and nothing about what was DELIVERED. So the only question that
matters about a memory system ("does anything ever read this?") has no answer, and the
answer people assume is the flattering one.

Measured 2026-09-01: 24 lessons, all agent-error, and no way to tell whether a single one
had ever reached an agent. Two of them described mistakes made hours earlier in a session
that never consulted them.

This is the failure mode the corpus itself warns about, in the lesson about trusting quiet
mechanisms: "Before believing a quiet mechanism, make it prove it ran: look for the artifact
it should have produced." Retrieval bookkeeping IS that artifact. Without it, every change
to how lessons are delivered - a hook, a brief, a different recall call - gets evaluated on
faith.

Two counters, written on the search path:
  retrieved_count   how many times this lesson came back in a result set
  last_retrieved_at when it last did

Deliberately NOT a separate events table: the question is "is this lesson dead weight", and
a count on the row answers it in one query. An events table answers questions nobody has yet
and costs a write amplification on a path that must stay cheap.
"""
from __future__ import annotations

import asyncpg
import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer  # noqa: E402

from axon.models.lesson import LessonRecord  # noqa: E402
from axon.store.lessons import LessonStore  # noqa: E402
from axon.store.vector_common import VECTOR_SIZE  # noqa: E402


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


@pytest.fixture
async def store(pg_dsn):
    s = LessonStore(dsn=pg_dsn)
    await s.init()
    con = await asyncpg.connect(pg_dsn)
    await con.execute("TRUNCATE lessons")
    await con.close()
    yield s


class _Engine:
    """Embeds any query to the same point, so ranking is not what these tests measure."""

    def embed_one(self, text: str) -> list[float]:
        return _vector(1.0)


def _vector(x: float) -> list[float]:
    v = [0.0] * VECTOR_SIZE
    v[0] = x
    return v


def _lesson(**over: object) -> LessonRecord:
    base = {
        "kind": "agent-error",
        "triggers": ["shell-script"],
        "mistake": "heredoc attached to the last command in a && chain",
        "tell": "the command hangs on stdin with no error",
        "fix": "give the heredoc its own command",
        "source": "forge/agent-errors",
        # search filters `vector IS NOT NULL`, so a lesson with no embedding
        # is never returned and never credited.
        "embedding": _vector(1.0),
    }
    return LessonRecord(**{**base, **over})  # type: ignore[arg-type]


async def _counts(dsn, lesson_id):
    con = await asyncpg.connect(dsn)
    try:
        return await con.fetchrow(
            "SELECT retrieved_count, last_retrieved_at FROM lessons WHERE id = $1", lesson_id
        )
    finally:
        await con.close()


@pytest.mark.asyncio
async def test_a_fresh_lesson_has_never_been_retrieved(store, pg_dsn):
    lesson = _lesson()
    await store.insert(lesson)
    row = await _counts(pg_dsn, lesson.id)
    assert row["retrieved_count"] == 0
    assert row["last_retrieved_at"] is None


@pytest.mark.asyncio
async def test_search_records_the_delivery(store, pg_dsn):
    lesson = _lesson()
    await store.insert(lesson)
    await store.search("a command that hangs waiting on stdin", engine=_Engine())
    row = await _counts(pg_dsn, lesson.id)
    assert row["retrieved_count"] == 1, "a delivered lesson still reads as never delivered"
    assert row["last_retrieved_at"] is not None


@pytest.mark.asyncio
async def test_the_count_accumulates(store, pg_dsn):
    lesson = _lesson()
    await store.insert(lesson)
    for _ in range(3):
        await store.search("hangs on stdin", engine=_Engine())
    row = await _counts(pg_dsn, lesson.id)
    assert row["retrieved_count"] == 3


@pytest.mark.asyncio
async def test_a_lesson_outside_the_result_set_is_not_credited(store, pg_dsn):
    """The point of the counter is to find dead weight. Crediting every row on every search
    would make everything look used, which is the state we are trying to leave."""
    wanted = _lesson()
    other = _lesson(kind="craft-lesson")
    await store.insert(wanted)
    await store.insert(other)
    await store.search("anything", engine=_Engine(), kind="agent-error")
    assert (await _counts(pg_dsn, wanted.id))["retrieved_count"] == 1
    assert (await _counts(pg_dsn, other.id))["retrieved_count"] == 0


@pytest.mark.asyncio
async def test_bookkeeping_failure_never_costs_the_caller_its_lessons(store, pg_dsn,
                                                                     monkeypatch):
    """Delivery must not depend on measuring delivery. If the counter write fails, the
    search still returns - the whole point is that asking is cheap and reliable."""
    async def boom(*a, **k):
        raise asyncpg.PostgresError("counter table is on fire")

    lesson = _lesson()
    await store.insert(lesson)
    monkeypatch.setattr(store, "_record_retrieval", boom, raising=False)
    results = await store.search("hangs on stdin", engine=_Engine())
    assert results, "a failed counter write swallowed the lessons themselves"
