"""Tests for the graph / decision methods on SessionStore."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from axon.core.decision import Decision
from axon.core.edge import Edge
from axon.store.session_store import SessionStore


@pytest.fixture(scope="module")
def pg_dsn():
    pytest.importorskip("testcontainers.postgres")
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


@pytest.fixture
async def store(
    pg_dsn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[SessionStore, None]:
    # Isolated per-test Postgres store via a fresh container + TRUNCATE.
    import asyncpg

    monkeypatch.setenv("AXON_PG_URL", pg_dsn)
    s = SessionStore(db_path=tmp_path / "axon.db")
    await s.init()
    await s._graph()
    await s._decisions()
    con = await asyncpg.connect(pg_dsn)
    await con.execute("TRUNCATE nodes, edges, decisions, adr")
    await con.close()
    yield s
    await s.close()


def _decision(**overrides: Any) -> Decision:
    base: dict[str, Any] = dict(
        id="dec-001",
        timestamp=datetime(2026, 5, 1, tzinfo=UTC),
        agent="claude-code",
        repo="axon",
        summary="s",
    )
    base.update(overrides)
    return Decision(**base)


async def test_add_node_and_query_subgraph_single(store: SessionStore) -> None:
    await store.add_node("a", "symbol", label="A")
    sg = await store.query_subgraph("a")
    assert sg["root"] == "a"
    assert sg["nodes"] == ["a"]
    assert sg["edges"] == []


async def test_add_edge_traversal(store: SessionStore) -> None:
    await store.add_edge(Edge(source_id="a", target_id="b", type="calls"))
    await store.add_edge(Edge(source_id="b", target_id="c", type="calls"))
    sg = await store.query_subgraph("a", depth=2)
    assert set(sg["nodes"]) == {"a", "b", "c"}
    assert len(sg["edges"]) == 2


async def test_query_subgraph_respects_depth(store: SessionStore) -> None:
    await store.add_edge(Edge(source_id="a", target_id="b", type="calls"))
    await store.add_edge(Edge(source_id="b", target_id="c", type="calls"))
    sg = await store.query_subgraph("a", depth=1)
    assert set(sg["nodes"]) == {"a", "b"}  # c is at depth 2, excluded
    assert len(sg["edges"]) == 1


async def test_shortest_path_finds_route(store: SessionStore) -> None:
    await store.add_edge(Edge(source_id="a", target_id="b", type="calls"))
    await store.add_edge(Edge(source_id="b", target_id="c", type="calls"))
    await store.add_edge(Edge(source_id="a", target_id="c", type="calls"))
    assert await store.shortest_path("a", "c") == ["a", "c"]


async def test_shortest_path_multi_hop(store: SessionStore) -> None:
    await store.add_edge(Edge(source_id="a", target_id="b", type="calls"))
    await store.add_edge(Edge(source_id="b", target_id="c", type="calls"))
    assert await store.shortest_path("a", "c") == ["a", "b", "c"]


async def test_shortest_path_no_route(store: SessionStore) -> None:
    await store.add_edge(Edge(source_id="a", target_id="b", type="calls"))
    assert await store.shortest_path("a", "z") is None


async def test_get_node_returns_payload(store: SessionStore) -> None:
    await store.add_node("a", "symbol", label="A", payload={"language": "python"})
    node = await store.get_node("a")
    assert node is not None and node["payload"] == {"language": "python"}
    assert await store.get_node("missing") is None


async def test_save_and_find_decision_by_symbol(store: SessionStore) -> None:
    decision = _decision(id="dec-010", symbols=["axon.core.decision.Decision"])
    await store.save_decision(decision)
    found = await store.find_decisions_by_symbol("axon.core.decision.Decision")
    assert len(found) == 1
    assert found[0] == decision


async def test_find_decision_by_symbol_no_match(store: SessionStore) -> None:
    await store.save_decision(_decision(id="dec-011", symbols=["other.Symbol"]))
    assert await store.find_decisions_by_symbol("axon.Nothing") == []


async def test_find_decisions_by_repo(store: SessionStore) -> None:
    await store.save_decision(_decision(id="dec-020", repo="axon"))
    await store.save_decision(_decision(id="dec-021", repo="axon"))
    await store.save_decision(_decision(id="dec-022", repo="other"))
    found = await store.find_decisions_by_repo("axon")
    assert {d.id for d in found} == {"dec-020", "dec-021"}


async def test_save_decision_replaces_on_same_id(store: SessionStore) -> None:
    await store.save_decision(_decision(id="dec-030", summary="first"))
    await store.save_decision(_decision(id="dec-030", summary="second"))
    found = await store.find_decisions_by_repo("axon")
    assert len(found) == 1
    assert found[0].summary == "second"
