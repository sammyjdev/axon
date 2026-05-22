"""Tests for the Redis subgraph cache on GraphStore (T2.3)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from axon.store.graph_store import GraphStore


@pytest.fixture
def redis_mock() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def graph_store(redis_mock: AsyncMock) -> GraphStore:
    store = GraphStore.__new__(GraphStore)
    store._redis = redis_mock
    return store


async def test_cache_subgraph_sets_with_ttl(
    graph_store: GraphStore, redis_mock: AsyncMock
) -> None:
    payload = {"root": "a", "nodes": ["a", "b"], "edges": []}
    await graph_store.cache_subgraph("a", payload, ttl=120)
    redis_mock.set.assert_awaited_once()
    args, kwargs = redis_mock.set.call_args
    assert args[0] == "subgraph:a"
    assert json.loads(args[1]) == payload
    assert kwargs["ex"] == 120


async def test_get_cached_subgraph_round_trip(
    graph_store: GraphStore, redis_mock: AsyncMock
) -> None:
    payload = {"root": "a", "nodes": ["a"], "edges": []}
    redis_mock.get.return_value = json.dumps(payload)
    assert await graph_store.get_cached_subgraph("a") == payload


async def test_get_cached_subgraph_missing_returns_none(
    graph_store: GraphStore, redis_mock: AsyncMock
) -> None:
    redis_mock.get.return_value = None
    assert await graph_store.get_cached_subgraph("missing") is None


async def test_invalidate_deletes_key(
    graph_store: GraphStore, redis_mock: AsyncMock
) -> None:
    await graph_store.invalidate("a")
    redis_mock.delete.assert_awaited_once_with("subgraph:a")


async def test_cache_subgraph_graceful_when_redis_down(
    graph_store: GraphStore, redis_mock: AsyncMock
) -> None:
    redis_mock.set.side_effect = RedisConnectionError("down")
    await graph_store.cache_subgraph("a", {"root": "a"})  # must not raise


async def test_get_cached_subgraph_graceful_when_redis_down(
    graph_store: GraphStore, redis_mock: AsyncMock
) -> None:
    redis_mock.get.side_effect = RedisConnectionError("down")
    assert await graph_store.get_cached_subgraph("a") is None


async def test_invalidate_graceful_when_redis_down(
    graph_store: GraphStore, redis_mock: AsyncMock
) -> None:
    redis_mock.delete.side_effect = RedisConnectionError("down")
    await graph_store.invalidate("a")  # must not raise
