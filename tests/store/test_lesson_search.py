"""LessonStore.search is Postgres full-text search: no embedding, no provider key.

Measured on the 41 real lessons on 2026-10-15 (`.specs/features/memory-direction/
lesson_search_compare.py`): 20 English queries written the way an agent asks before a
task put the expected lesson in the top 5 for 20 of 20, the same as pgvector, in about
5 ms instead of 600. Triggers weigh most, then the mistake, the tell and the fix.
"""
from __future__ import annotations

import asyncpg
import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer  # noqa: E402

from axon.models.lesson import LessonRecord  # noqa: E402
from axon.store.lessons import LessonStore  # noqa: E402


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


async def test_the_lesson_that_shares_the_query_words_comes_first(store):
    # Inserted first on purpose: a search that fell back to insertion order would
    # put the unrelated lesson first and this would catch it.
    await store.insert(
        _lesson(
            triggers=["imaplib"],
            mistake="assumed select() raises on a missing mailbox",
            tell="silently drops back to AUTH state",
            fix="check the typ returned explicitly",
        )
    )
    mapfile = await store.insert(_lesson())

    results = await store.search("reading command output into a bash array")

    assert [r.id for r in results][0] == mapfile


async def test_word_forms_match_through_stemming(store):
    wanted = await store.insert(
        _lesson(triggers=["python-test"], mistake="a shared helper overwrote the test fixture")
    )

    results = await store.search("helpers overwriting fixtures")

    assert [r.id for r in results] == [wanted]


async def test_a_trigger_outranks_the_same_word_in_the_fix(store):
    in_fix = await store.insert(
        _lesson(triggers=["http"], mistake="retried at once", tell="429", fix="add jitter")
    )
    in_trigger = await store.insert(
        _lesson(triggers=["jitter"], mistake="retried at once", tell="429", fix="back off")
    )

    results = await store.search("jitter")

    assert [r.id for r in results] == [in_trigger, in_fix]


async def test_a_query_sharing_no_word_returns_nothing(store):
    await store.insert(_lesson())

    assert await store.search("kubernetes ingress certificate") == []


async def test_a_query_of_only_stop_words_returns_nothing(store):
    await store.insert(_lesson())

    assert await store.search("the and of") == []


async def test_punctuation_in_the_query_is_not_an_operator(store):
    wanted = await store.insert(_lesson())

    results = await store.search("bash & | ! mapfile: (array) 'quoted' <-> a&b")

    assert [r.id for r in results] == [wanted]


async def test_search_filters_by_kind(store):
    await store.insert(_lesson(kind="agent-error"))
    craft_id = await store.insert(_lesson(kind="craft-lesson"))

    results = await store.search("mapfile", kind="craft-lesson")

    assert [r.id for r in results] == [craft_id]


async def test_search_filters_by_triggers_overlap(store):
    bash_id = await store.insert(_lesson(triggers=["bash"]))
    await store.insert(_lesson(triggers=["python"]))

    results = await store.search("mapfile", triggers=["bash"])

    assert [r.id for r in results] == [bash_id]


async def test_search_respects_limit(store):
    for i in range(3):
        await store.insert(_lesson(source=f"s{i}"))

    assert len(await store.search("mapfile", limit=2)) == 2
