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
            await con.execute("TRUNCATE nodes")
            await con.execute("TRUNCATE edges")
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


async def test_ordering_matches_codepoint(pg_dsn) -> None:
    """all_nodes and all_edges must return rows in codepoint (BINARY) order,
    matching SQLite BINARY collation. Postgres locale collation diverges for
    mixed-case / underscore ids so we need COLLATE "C" on ORDER BY."""
    from axon.store.pg_graph_repository import PostgresGraphRepository

    repo = PostgresGraphRepository(dsn=pg_dsn)
    try:
        await repo.ensure_schema()
        async with (await repo._ensure_pool()).acquire() as con:
            await con.execute("TRUNCATE nodes")
            await con.execute("TRUNCATE edges")

        # Insert in scrambled order so we cannot rely on insertion sequence.
        ids = ["Builder", "a", "_ac", "AuditLog", "Cache"]
        for nid in ids:
            await repo.add_node(nid, "symbol")

        # Python's sorted() on str uses codepoint order == SQLite BINARY.
        expected_node_order = sorted(ids)  # ["AuditLog","Builder","Cache","_ac","a"]
        actual_node_order = [n["id"] for n in await repo.all_nodes()]
        assert actual_node_order == expected_node_order, (
            f"all_nodes order mismatch: got {actual_node_order}, "
            f"expected {expected_node_order}"
        )

        # Add edges in scrambled (source, target, type) order.
        # Use valid Edge types; these still exercise collation on mixed-case ids.
        edge_tuples = [
            ("a", "Builder", "touches"),
            ("_ac", "Cache", "imports"),
            ("AuditLog", "_ac", "calls"),
        ]
        for src, tgt, etype in edge_tuples:
            await repo.add_edge(Edge(source_id=src, target_id=tgt, type=etype))

        expected_edge_order = sorted(edge_tuples)  # codepoint sort on (src, tgt, type)
        actual_edges = await repo.all_edges()
        actual_edge_tuples = [(e.source_id, e.target_id, e.type) for e in actual_edges]
        assert actual_edge_tuples == expected_edge_order, (
            f"all_edges order mismatch: got {actual_edge_tuples}, "
            f"expected {expected_edge_order}"
        )
    finally:
        await repo.close()
