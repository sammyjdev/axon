"""Coverage gap closed after Wave C/D review: nothing in the gate proved
``LessonStore.search`` orders by *cosine* distance specifically.

``test_lesson_search.py::test_search_orders_by_cosine_distance_to_the_query``
only exercises unit (one-hot) vectors. For unit vectors, L2 distance is a
monotonic function of cosine similarity (``||a-b||^2 = 2 - 2*cos(a,b)`` when
both are unit length), so cosine and L2 rank query results *identically* -
that test would still pass even if someone swapped the SQL's ``<=>``
(cosine) for ``<->`` (L2). It is not a decorative test, it just cannot see
this particular regression class.

This test uses non-unit vectors, chosen so the cosine ranking and the L2
ranking of the same two candidates genuinely disagree (verified by hand,
see the docstring on ``_QUERY``/``_COSINE_NEAR``/``_L2_NEAR`` below) - so it
fails if the operator is ever swapped, not just if the code stops sorting.
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


def _vector(x: float, y: float) -> list[float]:
    v = [0.0] * VECTOR_SIZE
    v[0], v[1] = x, y
    return v


# query = (1, 0).
# _COSINE_NEAR = (5, 0): same direction as query (cosine distance 0.0, the
#   closest possible) but far in magnitude (L2 distance 4.0).
# _L2_NEAR     = (1, 0.9): close in magnitude to query (L2 distance 0.9) but
#   off-direction (cosine distance ~0.257).
# Cosine ranks _COSINE_NEAR first (0.0 < 0.257); L2 ranks _L2_NEAR first
# (0.9 < 4.0) - the two metrics genuinely disagree on which is "closer".
_QUERY = _vector(1.0, 0.0)
_COSINE_NEAR = _vector(5.0, 0.0)
_L2_NEAR = _vector(1.0, 0.9)


class _FakeEngine:
    def __init__(self, table: dict[str, list[float]]) -> None:
        self._table = table

    def embed_one(self, text: str) -> list[float]:
        return self._table[text]


async def test_search_ranks_by_cosine_not_l2_distance(store):
    # the L2-near lesson goes in FIRST: an accidental `<=>` -> `<->` swap
    # would rank it first (0.9 < 4.0 under L2), not just fail to sort at all.
    l2_near_id = await store.insert(_lesson(embedding=_L2_NEAR, mistake="l2-near"))
    cosine_near_id = await store.insert(_lesson(embedding=_COSINE_NEAR, mistake="cosine-near"))

    engine = _FakeEngine({"q": _QUERY})
    results = await store.search("q", engine=engine)

    ids = [r.id for r in results]
    assert ids.index(cosine_near_id) < ids.index(l2_near_id)
