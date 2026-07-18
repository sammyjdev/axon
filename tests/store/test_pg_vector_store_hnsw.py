from __future__ import annotations

import asyncpg
import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer  # noqa: E402

from axon.store.pg_vector_store import PgVectorStore  # noqa: E402


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


async def _hnsw_indexes_on(dsn: str, table: str) -> list[str]:
    con = await asyncpg.connect(dsn)
    try:
        rows = await con.fetch(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = $1 AND indexdef ILIKE '%hnsw%'",
            table,
        )
        return [r["indexname"] for r in rows]
    finally:
        await con.close()


@pytest.mark.asyncio
async def test_hnsw_created_despite_name_collision(pg_dsn) -> None:
    """A rename-based migration can leave idx_<t>_hnsw on another table.

    ensure_collections must still give THIS table an HNSW index (observed
    in prod after the bge-m3 re-index: live table ran on seq scans).
    """
    con = await asyncpg.connect(pg_dsn)
    try:
        await con.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await con.execute("CREATE TABLE decoy_old (id text, vector vector(1024))")
        # Steal the canonical name for table emb_t before the store initializes.
        await con.execute(
            "CREATE INDEX idx_emb_t_hnsw ON decoy_old "
            "USING hnsw (vector vector_cosine_ops)"
        )
    finally:
        await con.close()

    store = PgVectorStore(pg_dsn, table="emb_t")
    await store.ensure_collections()
    await store.close()

    assert await _hnsw_indexes_on(pg_dsn, "emb_t"), (
        "emb_t must carry an HNSW index even when idx_emb_t_hnsw "
        "belongs to another table"
    )


@pytest.mark.asyncio
async def test_hnsw_normal_path_unchanged(pg_dsn) -> None:
    store = PgVectorStore(pg_dsn, table="emb_clean")
    await store.ensure_collections()
    await store.close()
    names = await _hnsw_indexes_on(pg_dsn, "emb_clean")
    assert names == ["idx_emb_clean_hnsw"]