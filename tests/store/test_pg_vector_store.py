from __future__ import annotations

import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer  # noqa: E402


@pytest.fixture(scope="module")
def pg_dsn():
    try:
        with PostgresContainer(
            "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
        ) as pg:
            # asyncpg DSN form
            yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
    except Exception as exc:
        pytest.skip(f"Postgres testcontainer unavailable: {exc}")


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


def _chunk(cid: str, ctx: str = "knowledge", file_path: str = "a.py", dim: int = None):
    from axon.store.vector_common import VECTOR_SIZE, Chunk
    n = dim or VECTOR_SIZE
    return Chunk(
        id=cid,
        vector=[0.1] * n,
        file_path=file_path,
        language="python",
        chunk_type="function",
        symbol="f",
        project="proj",
        ctx=ctx,
        content="def f(): pass",
    )


async def test_upsert_batch_inserts_and_is_idempotent(pg_dsn) -> None:
    from axon.store.pg_vector_store import PgVectorStore
    store = PgVectorStore(dsn=pg_dsn)
    try:
        await store.ensure_collections()
        await store.upsert_batch([_chunk("id-1"), _chunk("id-2")])
        await store.upsert_batch([_chunk("id-1")])  # same id -> update, no duplicate
        async with store._pool.acquire() as con:
            count = await con.fetchval("SELECT count(*) FROM embeddings")
        assert count == 2
    finally:
        await store.close()


async def test_search_round_trip_and_ctx_filter(pg_dsn) -> None:
    from axon.store.pg_vector_store import PgVectorStore
    from axon.store.vector_common import VECTOR_SIZE
    store = PgVectorStore(dsn=pg_dsn)
    try:
        await store.ensure_collections()
        # a "target" vector that is closest to the query
        target = _chunk("k-target", ctx="knowledge", file_path="t.py")
        target.vector = [1.0] + [0.0] * (VECTOR_SIZE - 1)
        other = _chunk("k-other", ctx="knowledge", file_path="o.py")
        other.vector = [0.0, 1.0] + [0.0] * (VECTOR_SIZE - 2)
        work = _chunk("w-secret", ctx="work", file_path="s.py")
        work.vector = [1.0] + [0.0] * (VECTOR_SIZE - 1)
        await store.upsert_batch([target, other, work])

        q = [1.0] + [0.0] * (VECTOR_SIZE - 1)
        hits = await store.search(q, collections=["knowledge"], top_k=5)
        ids = [h["id"] for h in hits]
        assert ids[0] == "k-target"          # closest first
        assert "w-secret" not in ids         # ctx filter: work never leaks into knowledge
        assert "modified_at" in hits[0]["payload"]
    finally:
        await store.close()


def test_invalid_table_name_raises() -> None:
    """PgVectorStore rejects invalid table names before making any connection."""
    from axon.store.pg_vector_store import PgVectorStore

    with pytest.raises(ValueError, match="invalid table name"):
        PgVectorStore(dsn="postgresql://x", table="bad-name; DROP")


async def test_table_isolation(pg_dsn) -> None:
    """Two stores on the same DSN with different table names must not share rows."""
    from axon.store.pg_vector_store import PgVectorStore
    from axon.store.vector_common import VECTOR_SIZE, Chunk

    store_a = PgVectorStore(dsn=pg_dsn, table="embeddings")
    store_b = PgVectorStore(dsn=pg_dsn, table="recall_embeddings")
    try:
        await store_a.ensure_collections()
        await store_b.ensure_collections()

        # Truncate both tables so this test is self-contained
        async with store_a._pool.acquire() as con:
            await con.execute("TRUNCATE embeddings")
        async with store_b._pool.acquire() as con:
            await con.execute("TRUNCATE recall_embeddings")

        chunk = Chunk(
            id="iso-1",
            vector=[1.0] + [0.0] * (VECTOR_SIZE - 1),
            file_path="iso.py",
            language="python",
            chunk_type="function",
            symbol="iso_fn",
            project="iso",
            ctx="knowledge",
            content="def iso_fn(): pass",
        )
        await store_a.upsert_batch([chunk])

        # The chunk must NOT be visible via store_b's search
        hits = await store_b.search(
            query_vector=[1.0] + [0.0] * (VECTOR_SIZE - 1),
            collections=["knowledge"],
            top_k=5,
        )
        ids = [h["id"] for h in hits]
        assert "iso-1" not in ids, "chunk from embeddings table leaked into recall_embeddings"

        # And must be visible via store_a
        hits_a = await store_a.search(
            query_vector=[1.0] + [0.0] * (VECTOR_SIZE - 1),
            collections=["knowledge"],
            top_k=5,
        )
        ids_a = [h["id"] for h in hits_a]
        assert "iso-1" in ids_a, "chunk not found in the table it was upserted into"
    finally:
        await store_a.close()
        await store_b.close()


async def test_delete_by_file_removes_only_that_file(pg_dsn) -> None:
    from axon.store.pg_vector_store import PgVectorStore
    store = PgVectorStore(dsn=pg_dsn)
    try:
        await store.ensure_collections()
        # clear any rows left by earlier tests so this test is self-contained
        async with store._pool.acquire() as con:
            await con.execute("TRUNCATE embeddings")
        await store.upsert_batch([
            _chunk("a1", ctx="knowledge", file_path="a.py"),
            _chunk("a2", ctx="knowledge", file_path="a.py"),
            _chunk("b1", ctx="knowledge", file_path="b.py"),
        ])
        await store.delete_by_file("knowledge", "a.py")
        async with store._pool.acquire() as con:
            remaining = await con.fetch("SELECT id FROM embeddings ORDER BY id")
        assert [r["id"] for r in remaining] == ["b1"]
    finally:
        await store.close()


async def test_hybrid_search_exact_term_can_outrank_dense(pg_dsn, monkeypatch) -> None:
    from axon.store.pg_vector_store import PgVectorStore
    from axon.store.vector_common import VECTOR_SIZE

    store = PgVectorStore(dsn=pg_dsn)
    try:
        await store.ensure_collections()
        async with store._pool.acquire() as con:
            await con.execute("TRUNCATE embeddings")

        exact = _chunk("exact", file_path="docs/dec-040-density.md")
        exact.vector = [0.0, 1.0] + [0.0] * (VECTOR_SIZE - 2)
        exact.content = "density gate dec-040 AXON_MAX_PRE_SEND_TOKENS"
        dense = _chunk("dense", file_path="docs/unrelated.md")
        dense.vector = [1.0] + [0.0] * (VECTOR_SIZE - 1)
        dense.content = "unrelated semantic neighbor"
        await store.upsert_batch([exact, dense])

        query_vector = [1.0] + [0.0] * (VECTOR_SIZE - 1)
        monkeypatch.delenv("AXON_HYBRID_SEARCH", raising=False)
        dense_hits = await store.search(query_vector, collections=["knowledge"], top_k=2)
        monkeypatch.setenv("AXON_HYBRID_SEARCH", "1")
        hybrid_hits = await store.search(
            query_vector,
            query="density gate dec-040",
            collections=["knowledge"],
            top_k=2,
        )

        assert dense_hits[0]["id"] == "dense"
        assert hybrid_hits[0]["id"] == "exact"
    finally:
        await store.close()
