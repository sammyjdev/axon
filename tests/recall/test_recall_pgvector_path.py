from __future__ import annotations

import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer  # noqa: E402


@pytest.fixture(scope="module")
def pg_dsn():
    container = PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    )
    with container as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


async def test_recall_harness_runs_against_pgvector(pg_dsn, monkeypatch) -> None:
    monkeypatch.setenv("AXON_VECTOR_BACKEND", "pgvector")
    monkeypatch.setenv("AXON_PG_URL", pg_dsn)
    from axon.benchmark.recall import index_corpus_pg_smoke  # thin helper added in Step 3

    # index two chunks via the pgvector path and confirm a query returns the closer one
    top_id = await index_corpus_pg_smoke(pg_dsn)
    assert top_id == "near"


async def test_run_recall_guard_pg_scores_expected(pg_dsn) -> None:
    """Verify run_recall_guard_pg ranks the target chunk first with a mock engine."""
    from unittest.mock import MagicMock

    from axon.benchmark.recall import run_recall_guard_pg
    from axon.store.pg_vector_store import PgVectorStore
    from axon.store.vector_store import VECTOR_SIZE, Chunk

    store = PgVectorStore(dsn=pg_dsn)
    try:
        await store.ensure_collections()
        # Truncate to isolate from other tests sharing the same module-scoped container
        async with store._pool.acquire() as con:
            await con.execute("TRUNCATE embeddings")

        target_vector = [1.0] + [0.0] * (VECTOR_SIZE - 1)
        distractor_vector = [0.0, 1.0] + [0.0] * (VECTOR_SIZE - 2)

        target_chunk = Chunk(
            id="target",
            vector=target_vector,
            file_path="m.py",
            language="python",
            chunk_type="function",
            symbol="match_fn",
            project="recall",
            ctx="knowledge",
            content="def match_fn(): pass",
        )
        distractor_chunk = Chunk(
            id="distractor",
            vector=distractor_vector,
            file_path="o.py",
            language="python",
            chunk_type="function",
            symbol="other_fn",
            project="recall",
            ctx="knowledge",
            content="def other_fn(): pass",
        )
        await store.upsert_batch([target_chunk, distractor_chunk])

        mock_engine = MagicMock()
        mock_engine.embed_one.return_value = [1.0] + [0.0] * (VECTOR_SIZE - 1)

        golden_set = [
            {
                "id": "q1",
                "query": "anything",
                "expected_file": "m.py",
                "expected_symbol": "match_fn",
                "min_score": 0.5,
            }
        ]

        summary, metrics = await run_recall_guard_pg(golden_set, mock_engine, store)

        assert metrics["recall_top1"] == 1.0
        assert metrics["results_by_query"]["q1"]["rank"] == 1
    finally:
        await store.close()
