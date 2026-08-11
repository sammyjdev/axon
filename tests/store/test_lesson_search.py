"""Wave C Task 6: LessonStore.search by cosine distance.

Two arms, deliberately split:

* The mechanism (ORDER BY <=>, kind filter, triggers filter, limit, excluding
  rows with no vector) is proven fast and deterministically with a fake engine
  that returns hand-picked orthogonal vectors - no network, always runs in the
  gate.
* The claim ``search()`` exists to make good on - PARAPHRASE retrieval, not
  lexical matching in disguise - can only be proven with real semantics. A
  fake or hash-derived vector would make that assertion vacuous: it would pass
  while proving nothing about paraphrase, which is the entire reason this task
  exists over ``axon_search`` (see the module docstring in
  ``axon/store/lessons.py``). That test goes through the real bge-m3 chain
  (Ollama is not run locally on this machine, so it falls through to
  DeepInfra - see ``providers.py``) and is opt-in via AXON_RUN_LESSON_SEARCH=1,
  matching the precedent already set by
  ``tests/recall/test_recall_guard.py``'s AXON_RUN_RECALL gate: a
  skipped-by-default test that is genuinely semantic beats a fast one that
  tests nothing.
"""
from __future__ import annotations

import os

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


class _FakeEngine:
    """Deterministic, orthogonal vectors - proves the SQL, not semantics."""

    def __init__(self, table: dict[str, list[float]]) -> None:
        self._table = table

    def embed_one(self, text: str) -> list[float]:
        return self._table[text]


def _one_hot(index: int) -> list[float]:
    vector = [0.0] * VECTOR_SIZE
    vector[index] = 1.0
    return vector


async def test_search_orders_by_cosine_distance_to_the_query(store):
    # far inserted FIRST on purpose: a search that quietly fell back to
    # insertion order (e.g. a dropped ORDER BY) would put it first too, and
    # this assertion would still catch that instead of passing by accident.
    far_id = await store.insert(_lesson(embedding=_one_hot(1), mistake="far"))
    near_id = await store.insert(_lesson(embedding=_one_hot(0), mistake="near"))

    engine = _FakeEngine({"q": _one_hot(0)})
    results = await store.search("q", engine=engine)

    ids = [r.id for r in results]
    assert ids.index(near_id) < ids.index(far_id)


async def test_search_filters_by_kind(store):
    await store.insert(_lesson(embedding=_one_hot(0), kind="agent-error", mistake="a"))
    craft_id = await store.insert(
        _lesson(embedding=_one_hot(0), kind="craft-lesson", mistake="b")
    )

    engine = _FakeEngine({"q": _one_hot(0)})
    results = await store.search("q", engine=engine, kind="craft-lesson")

    assert [r.id for r in results] == [craft_id]


async def test_search_filters_by_triggers_overlap(store):
    bash_id = await store.insert(_lesson(embedding=_one_hot(0), triggers=["bash"], mistake="a"))
    await store.insert(_lesson(embedding=_one_hot(0), triggers=["python"], mistake="b"))

    engine = _FakeEngine({"q": _one_hot(0)})
    results = await store.search("q", engine=engine, triggers=["bash"])

    assert [r.id for r in results] == [bash_id]


async def test_search_respects_limit(store):
    for i in range(3):
        await store.insert(_lesson(embedding=_one_hot(0), mistake=f"m{i}"))

    engine = _FakeEngine({"q": _one_hot(0)})
    results = await store.search("q", engine=engine, limit=2)

    assert len(results) == 2


async def test_search_excludes_lessons_with_no_embedding(store):
    await store.insert(_lesson(embedding=None, mistake="no-vector"))
    with_vector_id = await store.insert(_lesson(embedding=_one_hot(0), mistake="has-vector"))

    engine = _FakeEngine({"q": _one_hot(0)})
    results = await store.search("q", engine=engine)

    assert [r.id for r in results] == [with_vector_id]


@pytest.mark.skipif(
    os.environ.get("AXON_RUN_LESSON_SEARCH") != "1",
    reason="set AXON_RUN_LESSON_SEARCH=1 to run the real bge-m3 paraphrase gate",
)
async def test_paraphrased_query_ranks_the_paraphrased_lesson_first(store):
    """The point of this task: axon_search already matches literal text and
    misses exactly this. Proves the store finds a lesson whose WORDS the query
    never uses.
    """
    from axon.embedder.engine import EmbedderEngine
    from axon.embedder.lesson_embedding import embed_lesson
    from axon.models.lesson import LessonCreate

    engine = EmbedderEngine()

    mapfile_lesson = LessonCreate(
        kind="agent-error",
        triggers=["shell-script", "bash"],
        mistake="used mapfile, which bash 3.2 on macOS does not have",
        tell="line 3: mapfile: command not found",
        fix="read -r -d '' into an array instead",
        source="forge/agent-errors",
    )
    unrelated_lesson = LessonCreate(
        kind="craft-lesson",
        triggers=["imaplib", "email"],
        mistake="assumed IMAP4.select() raises on a missing mailbox",
        tell="silently drops back to AUTH state instead of raising",
        fix="check the typ returned by select()/search() explicitly",
        source="merit/mail.py",
    )

    # unrelated_lesson goes in FIRST on purpose: an implementation that quietly
    # degraded to insertion order (or any order not driven by the vector)
    # would surface it first, not the paraphrased match, and get caught.
    await store.insert(
        LessonRecord(
            **unrelated_lesson.model_dump(),
            embedding=embed_lesson(unrelated_lesson, engine=engine),
        )
    )
    mapfile_id = await store.insert(
        LessonRecord(
            **mapfile_lesson.model_dump(),
            embedding=embed_lesson(mapfile_lesson, engine=engine),
        )
    )

    # Paraphrase: avoids the one term axon_search's literal match relies on
    # ("mapfile") plus "array"/"command not found" - a keyword match on the
    # distinctive term would miss this entirely, even though "bash"/"shell
    # script" survive as the ordinary shared vocabulary a real paraphrase has.
    results = await store.search(
        "a bash script failed because an old shell version lacks a newer "
        "builtin for reading lines of output straight into a list variable",
        engine=engine,
    )

    assert results
    assert results[0].id == mapfile_id
