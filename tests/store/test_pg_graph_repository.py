from __future__ import annotations

import pytest

pytest.importorskip("testcontainers.postgres")
from testcontainers.postgres import PostgresContainer  # noqa: E402

from axon.core.edge import Edge  # noqa: E402


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


async def test_add_node_upsert_and_get(pg_dsn) -> None:
    from axon.store.pg_graph_repository import PostgresGraphRepository

    repo = PostgresGraphRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        await repo.ensure_schema()  # idempotent
        await repo.add_node("s1", "symbol", label="foo", payload={"k": 1})
        await repo.add_node("s1", "symbol", label="foo2", payload={"k": 2})  # upsert
        node = await repo.get_node("s1")
        assert node["label"] == "foo2" and node["payload"] == {"k": 2}
        assert await repo.get_node("missing") is None
    finally:
        await repo.close()


async def test_add_edge_idempotent_and_queries(pg_dsn) -> None:
    from axon.store.pg_graph_repository import PostgresGraphRepository

    repo = PostgresGraphRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        async with (await repo._ensure_pool()).acquire() as con:
            await con.execute("TRUNCATE nodes"); await con.execute("TRUNCATE edges")
        for nid in ("a", "b", "c"):
            await repo.add_node(nid, "symbol")
        await repo.add_edge(Edge(source_id="a", target_id="b", type="touches"))
        await repo.add_edge(Edge(source_id="a", target_id="b", type="touches"))  # dup ignored
        await repo.add_edge(Edge(source_id="b", target_id="c", type="touches"))
        sub = await repo.query_subgraph("a", depth=2)
        assert sub["root"] == "a" and set(sub["nodes"]) == {"a", "b", "c"}
        assert len(sub["edges"]) == 2  # no duplicate a->b
        assert await repo.shortest_path("a", "c") == ["a", "b", "c"]
        assert await repo.shortest_path("c", "a") is None
        assert [n["id"] for n in await repo.all_nodes()] == ["a", "b", "c"]
        assert len(await repo.all_edges()) == 2
    finally:
        await repo.close()
