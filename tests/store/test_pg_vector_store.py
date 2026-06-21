from __future__ import annotations

import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer  # noqa: E402


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer("pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon") as pg:
        # asyncpg DSN form
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


async def test_ensure_collections_idempotent(pg_dsn) -> None:
    from axon.store.pg_vector_store import PgVectorStore

    store = PgVectorStore(dsn=pg_dsn)
    try:
        await store.ensure_collections()
        await store.ensure_collections()  # second run must be a no-op
        # extension + table exist
        async with store._pool.acquire() as con:
            ext = await con.fetchval("SELECT 1 FROM pg_extension WHERE extname='vector'")
            tbl = await con.fetchval("SELECT to_regclass('public.embeddings')")
        assert ext == 1
        assert tbl is not None
    finally:
        await store.close()
