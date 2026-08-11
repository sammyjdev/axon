"""Wave C Task 8: the dimension guard covers the lessons table.

Goes through ``LessonStore.init()`` end to end, not the guard function in
isolation (that unit-level shape already exists for PgVectorStore in
``test_pg_vector_dim_guard.py`` and would prove nothing new here). The
regression that matters is ``init()`` proceeding to DDL against a table whose
``vector`` column is already a different dimension - so this test builds that
table for real, in the container, before pointing a fresh store at it.
"""
from __future__ import annotations

import asyncpg
import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer  # noqa: E402

from axon.store.lessons import LessonStore  # noqa: E402
from axon.store.vector_common import VECTOR_SIZE  # noqa: E402

_WRONG_DIM = VECTOR_SIZE + 1


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


async def test_init_raises_when_the_lessons_table_is_already_a_different_dim(pg_dsn):
    # Build the mismatched state honestly: a real lessons table, at a
    # dimension init() never chose, sitting in the container before init()
    # ever runs.
    con = await asyncpg.connect(pg_dsn)
    try:
        await con.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await con.execute(f"CREATE TABLE lessons (vector vector({_WRONG_DIM}))")
    finally:
        await con.close()

    store = LessonStore(dsn=pg_dsn)

    with pytest.raises(ValueError, match=str(_WRONG_DIM)) as exc_info:
        await store.init()

    msg = str(exc_info.value)
    assert str(_WRONG_DIM) in msg
    assert str(VECTOR_SIZE) in msg
