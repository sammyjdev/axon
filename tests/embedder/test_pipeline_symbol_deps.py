"""dec-121 Phase 2: the indexer writes the call-graph to Postgres symbol_deps.

Exercises the exact writer contract the pipeline performs
(build_dependency_records -> PostgresSymbolDeps.upsert_deps) against a real
container, without standing up the embedding engine + vector store.
"""
from __future__ import annotations

import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer  # noqa: E402

from axon.embedder.chunker import Chunk  # noqa: E402
from axon.embedder.graph_extractor import build_dependency_records  # noqa: E402
from axon.store.pg_symbol_deps import PostgresSymbolDeps  # noqa: E402


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


def _chunk(symbol: str, content: str) -> Chunk:
    return Chunk(
        symbol=symbol,
        chunk_type="method",
        start_line=1,
        end_line=len(content.splitlines()) or 1,
        content=content,
        file_path=f"/tmp/{symbol}",
        language="python",
    )


async def test_indexer_writes_call_graph_to_symbol_deps(pg_dsn):
    store = PostgresSymbolDeps(dsn=pg_dsn)
    await store.ensure_schema()
    try:
        chunks = [
            _chunk("handler", "def handler():\n    prepare()\n"),
            _chunk("prepare", "def prepare():\n    return 1\n"),
        ]
        # Mirror the pipeline's dep-write loop (pipeline.py index_path).
        for record in build_dependency_records(chunks):
            await store.upsert_deps(
                record.symbol, calls=record.calls, called_by=record.called_by
            )

        assert await store.get_subgraph("handler") == {
            "exists": True,
            "calls": ["prepare"],
            "called_by": [],
        }
        assert await store.get_subgraph("prepare") == {
            "exists": True,
            "calls": [],
            "called_by": ["handler"],
        }
    finally:
        await store.close()
