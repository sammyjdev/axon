"""Wave B Task 4: LessonStore.insert."""
from __future__ import annotations

from uuid import uuid4

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


def _lesson(**over: object) -> LessonRecord:
    base = {
        "kind": "agent-error",
        "triggers": ["shell-script", "bash"],
        "mistake": "used mapfile, which bash 3.2 does not have",
        "tell": "line 3: mapfile: command not found",
        "fix": "read -r -d '' into an array",
        "source": "forge/agent-errors",
    }
    return LessonRecord(**{**base, **over})  # type: ignore[arg-type]


async def test_inserted_lesson_comes_back_by_id_with_triggers_intact(store):
    lesson = _lesson(triggers=["shell-script", "bash"])

    lesson_id = await store.insert(lesson)
    got = await store.get(lesson_id)

    assert got is not None
    assert got.id == lesson_id
    assert got.triggers == ["shell-script", "bash"]
    assert got.kind == "agent-error"
    assert got.mistake == lesson.mistake
    assert got.tell == lesson.tell
    assert got.fix == lesson.fix
    assert got.source == lesson.source


async def test_a_lesson_round_trips_its_vector(store):
    """The column that broke Wave A on merge, exercised rather than assumed.

    Creating a ``vector`` column proves nothing on its own: the failure mode was
    a database where the extension was never installed. Writing and reading one
    is what proves the bootstrap in ``init`` actually ran.
    """
    embedding = [0.0] * VECTOR_SIZE
    embedding[0] = 1.0

    lesson_id = await store.insert(_lesson(embedding=embedding))
    got = await store.get(lesson_id)

    assert got is not None
    assert got.embedding is not None
    assert len(got.embedding) == VECTOR_SIZE
    assert got.embedding[0] == pytest.approx(1.0)


async def test_a_lesson_without_an_embedding_is_allowed(store):
    """Task 4 inserts; embedding arrives with the recorder in Wave D."""
    lesson_id = await store.insert(_lesson())
    got = await store.get(lesson_id)

    assert got is not None
    assert got.embedding is None


async def test_get_returns_none_for_an_unknown_id(store):
    assert await store.get(uuid4()) is None


async def test_get_by_source_finds_the_matching_row(store):
    lesson = _lesson(source="agent-errors.json#bash32-no-mapfile")
    await store.insert(lesson)

    got = await store.get_by_source("agent-errors.json#bash32-no-mapfile")

    assert got is not None
    assert got.id == lesson.id


async def test_get_by_source_returns_none_for_an_unknown_source(store):
    assert await store.get_by_source("agent-errors.json#nope") is None


async def test_update_overwrites_content_but_keeps_id_and_created_at(store):
    lesson = _lesson(source="agent-errors.json#bash32-no-mapfile")
    await store.insert(lesson)

    updated = _lesson(
        id=lesson.id,
        created_at=lesson.created_at,
        source="agent-errors.json#bash32-no-mapfile",
        fix="a corrected fix",
    )
    await store.update(updated)
    got = await store.get(lesson.id)

    assert got is not None
    assert got.id == lesson.id
    assert got.created_at == lesson.created_at
    assert got.fix == "a corrected fix"
