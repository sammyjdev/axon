from __future__ import annotations

import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer  # noqa: E402


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer("pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon") as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


async def test_recall_harness_runs_against_pgvector(pg_dsn, monkeypatch) -> None:
    monkeypatch.setenv("AXON_VECTOR_BACKEND", "pgvector")
    monkeypatch.setenv("AXON_PG_URL", pg_dsn)
    from axon.benchmark.recall import index_corpus_pg_smoke  # thin helper added in Step 3

    # index two chunks via the pgvector path and confirm a query returns the closer one
    top_id = await index_corpus_pg_smoke(pg_dsn)
    assert top_id == "near"
