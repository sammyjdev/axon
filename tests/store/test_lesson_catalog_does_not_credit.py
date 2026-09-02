"""A catalogue is not a delivery, and building it on `search()` would say it was.

The point of the catalogue is to tell an agent that lessons EXIST, with enough of a handle
(kind + triggers) to decide whether to ask - the content stays behind `search_lessons`. It
is rendered into `.axon/context.md`, which a SessionStart hook cats on every session.

That is exactly where it can destroy its own measurement. `LessonStore.search()` credits
`retrieved_count` for every row it returns, deliberately: that counter is how "does anything
ever read this?" got an answer. A catalogue built on `search()` would credit five lessons
per session start, forever, and the baseline of 24 lessons / 0 deliveries - landed the same
day - would be gone within a day, replaced by a number that measures the hook rather than
any agent's decision to consult one.

So the catalogue reads through its own query, and this file exists to keep it that way.
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


def _vector(x: float) -> list[float]:
    v = [0.0] * VECTOR_SIZE
    v[0] = x
    return v


def _lesson(**over: object) -> LessonRecord:
    base = {
        "kind": "agent-error",
        "triggers": ["shell-script", "heredoc"],
        "mistake": "heredoc attached to the last command in a && chain",
        "tell": "the command hangs on stdin with no error",
        "fix": "give the heredoc its own command",
        "source": "forge/agent-errors",
        "embedding": _vector(1.0),
    }
    return LessonRecord(**{**base, **over})  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_the_catalogue_does_not_credit_a_single_lesson(store, pg_dsn):
    """The whole reason this method exists instead of a call to `search()`."""
    lesson = _lesson()
    await store.insert(lesson)

    await store.catalog()

    con = await asyncpg.connect(pg_dsn)
    try:
        row = await con.fetchrow(
            "SELECT retrieved_count, last_retrieved_at FROM lessons WHERE id = $1", lesson.id
        )
    finally:
        await con.close()
    assert row["retrieved_count"] == 0, (
        "listing a lesson counted as delivering it - the baseline this measures is gone")
    assert row["last_retrieved_at"] is None


@pytest.mark.asyncio
async def test_the_catalogue_carries_the_handle_and_not_the_content(store):
    """kind + triggers are enough to decide whether to ask. The mistake/tell/fix are the
    reason to call `search_lessons`, and putting them here would make the call pointless
    while costing every session the whole corpus in tokens."""
    await store.insert(_lesson())
    entries = await store.catalog()

    assert len(entries) == 1
    entry = entries[0]
    assert entry.kind == "agent-error"
    assert entry.triggers == ["shell-script", "heredoc"]
    assert not hasattr(entry, "mistake")
    assert not hasattr(entry, "fix")


@pytest.mark.asyncio
async def test_a_lesson_with_no_vector_is_still_catalogued(store):
    """`search()` filters `vector IS NOT NULL` because an unembedded lesson can never be
    nearest to anything. The catalogue is not a ranking, so excluding it there would hide a
    lesson that exists - and hiding it is the failure this whole item is about."""
    await store.insert(_lesson(embedding=None))
    assert len(await store.catalog()) == 1
