"""
Testes unitários dos stores — T-034.
graph_store e session_store usam Testcontainers.
collections.py é testado sem infra (lógica pura).
vector_store requer Qdrant real — testa apenas a lógica de agrupamento/batch.
"""

import json
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest

from prometheus.store.collections import get_search_collections
from prometheus.store.graph_store import GraphStore
from prometheus.store.session_store import ADR, CodeChange, SessionMemory, SessionStore

# ── collections.py ─────────────────────────────────────────────────────────────


class TestGetSearchCollections:
    def test_no_ctx_excludes_work(self) -> None:
        result = get_search_collections(None)
        assert "work" not in result
        assert set(result) == {"personal", "career", "knowledge", "saas"}

    def test_explicit_work_ctx_returns_only_work(self) -> None:
        result = get_search_collections("work")
        assert result == ["work"]

    def test_personal_ctx_excludes_work(self) -> None:
        result = get_search_collections("personal")
        assert result == ["personal"]

    def test_explicit_non_work_ctx_returns_only_that_context(self) -> None:
        assert get_search_collections("knowledge") == ["knowledge"]
        assert get_search_collections("career") == ["career"]
        assert get_search_collections("saas") == ["saas"]

    def test_empty_string_ctx_excludes_work(self) -> None:
        result = get_search_collections("")
        assert "work" not in result


# ── graph_store.py ─────────────────────────────────────────────────────────────


@pytest.fixture
def redis_mock():
    mock = AsyncMock()
    mock.ping = AsyncMock()
    mock.hset = AsyncMock()
    mock.hget = AsyncMock()
    mock.hgetall = AsyncMock()
    mock.delete = AsyncMock()
    mock.aclose = AsyncMock()
    return mock


@pytest.fixture
def graph_store(redis_mock):
    store = GraphStore.__new__(GraphStore)
    store._redis = redis_mock
    return store


@pytest.mark.asyncio
class TestGraphStore:
    async def test_connect_pings_redis(self, graph_store, redis_mock) -> None:
        await graph_store.connect()
        redis_mock.ping.assert_awaited_once()

    async def test_upsert_deps_calls_hset(self, graph_store, redis_mock) -> None:
        await graph_store.upsert_deps(
            "OrderService",
            calls=["PaymentService", "NotificationService"],
            called_by=["OrderController"],
        )
        redis_mock.hset.assert_awaited_once()
        call_kwargs = redis_mock.hset.call_args
        assert call_kwargs[0][0] == "dep:OrderService"
        mapping = call_kwargs[1]["mapping"]
        assert json.loads(mapping["calls"]) == ["PaymentService", "NotificationService"]
        assert json.loads(mapping["called_by"]) == ["OrderController"]

    async def test_get_calls_returns_list(self, graph_store, redis_mock) -> None:
        redis_mock.hget.return_value = json.dumps(["PaymentService"])
        result = await graph_store.get_calls("OrderService")
        assert result == ["PaymentService"]

    async def test_get_calls_returns_empty_when_missing(self, graph_store, redis_mock) -> None:
        redis_mock.hget.return_value = None
        result = await graph_store.get_calls("UnknownSymbol")
        assert result == []

    async def test_get_deps_returns_both_directions(self, graph_store, redis_mock) -> None:
        redis_mock.hgetall.return_value = {
            "calls": json.dumps(["A"]),
            "called_by": json.dumps(["B"]),
        }
        deps = await graph_store.get_deps("OrderService")
        assert deps["calls"] == ["A"]
        assert deps["called_by"] == ["B"]

    async def test_get_deps_returns_empty_when_missing(self, graph_store, redis_mock) -> None:
        redis_mock.hgetall.return_value = {}
        deps = await graph_store.get_deps("Unknown")
        assert deps == {"calls": [], "called_by": []}

    async def test_get_subgraph_for_missing_symbol(self, graph_store, redis_mock) -> None:
        redis_mock.hgetall.return_value = {}
        subgraph = await graph_store.get_subgraph("Unknown")
        assert subgraph["exists"] is False
        assert subgraph["calls"] == []
        assert subgraph["called_by"] == []

    async def test_delete_removes_key(self, graph_store, redis_mock) -> None:
        await graph_store.delete("OrderService")
        redis_mock.delete.assert_awaited_once_with("dep:OrderService")

    async def test_traverse_respects_depth_and_nodes(self, graph_store, redis_mock) -> None:
        calls_map = {
            "dep:A": json.dumps(["B", "C"]),
            "dep:B": json.dumps(["D"]),
            "dep:C": json.dumps([]),
            "dep:D": json.dumps([]),
        }

        def _fake_hget(key: str, field: str):
            if field != "calls":
                return None
            return calls_map.get(key)

        redis_mock.hget.side_effect = _fake_hget

        traversal = await graph_store.traverse("A", max_depth=2, max_nodes=3)
        assert traversal["root"] == "A"
        assert len(traversal["nodes"]) <= 3


# ── session_store.py ────────────────────────────────────────────────────────────


@pytest.fixture
async def session_store(tmp_path) -> AsyncGenerator[SessionStore, None]:
    store = SessionStore(db_path=tmp_path / "test.db")
    await store.init()
    yield store
    await store.close()


@pytest.mark.asyncio
class TestSessionStore:
    async def test_save_and_get_adr(self, session_store) -> None:
        adr = ADR(
            project="aerus-rpg",
            title="Usar event sourcing para combate",
            context="Precisamos replay de estados",
            decision="Event sourcing com Redis Streams",
            rationale="Facilita undo/redo e replay",
        )
        adr_id = await session_store.save_adr(adr)
        assert adr_id > 0

        adrs = await session_store.get_adrs("aerus-rpg")
        assert len(adrs) == 1
        assert adrs[0].title == "Usar event sourcing para combate"
        assert adrs[0].project == "aerus-rpg"

    async def test_get_adrs_empty_project(self, session_store) -> None:
        adrs = await session_store.get_adrs("nonexistent")
        assert adrs == []

    async def test_save_and_get_session_memory(self, session_store) -> None:
        mem = SessionMemory(
            project="aerus-rpg",
            summary="Implementamos o sistema de combate com turnos.",
            raw_turns=15,
        )
        mem_id = await session_store.save_session_memory(mem)
        assert mem_id > 0

        mems = await session_store.get_session_memories("aerus-rpg")
        assert len(mems) == 1
        assert mems[0].raw_turns == 15
        assert "combate" in mems[0].summary

    async def test_session_memory_respects_limit(self, session_store) -> None:
        for i in range(5):
            await session_store.save_session_memory(
                SessionMemory(project="p", summary=f"session {i}", raw_turns=i)
            )
        mems = await session_store.get_session_memories("p", limit=3)
        assert len(mems) == 3

    async def test_save_and_get_code_change(self, session_store) -> None:
        change = CodeChange(
            commit_hash="abc123",
            file_path="src/combat/Engine.java",
            diff_summary="Added turn-based combat loop",
            why="feat: implement combat engine",
        )
        await session_store.save_code_change(change)

        changes = await session_store.get_recent_changes("src/combat/Engine.java")
        assert len(changes) == 1
        assert changes[0].commit_hash == "abc123"

    async def test_code_change_upsert_on_duplicate_key(self, session_store) -> None:
        change = CodeChange(
            commit_hash="abc123",
            file_path="src/Engine.java",
            diff_summary="v1",
        )
        await session_store.save_code_change(change)

        change2 = CodeChange(
            commit_hash="abc123",
            file_path="src/Engine.java",
            diff_summary="v2 updated",
        )
        await session_store.save_code_change(change2)

        changes = await session_store.get_recent_changes("src/Engine.java")
        assert len(changes) == 1
        assert changes[0].diff_summary == "v2 updated"

    async def test_get_recent_changes_empty(self, session_store) -> None:
        changes = await session_store.get_recent_changes("nonexistent.java")
        assert changes == []
