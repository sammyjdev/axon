"""Tests for GraphStore.upsert_deps_batch (Plan C T5).

Uses a fake Redis pipeline to avoid requiring a live Redis in unit tests.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from axon.embedder.graph_extractor import DependencyRecord
from axon.store.graph_store import GraphStore


def _make_record(symbol: str, calls: list[str], called_by: list[str]) -> DependencyRecord:
    return DependencyRecord(symbol=symbol, calls=calls, called_by=called_by)


async def test_empty_batch_is_noop() -> None:
    store = GraphStore.__new__(GraphStore)
    store._redis = AsyncMock()
    await store.upsert_deps_batch([])
    store._redis.pipeline.assert_not_called()


async def test_single_record_uses_one_pipeline_execute() -> None:
    store = GraphStore.__new__(GraphStore)
    pipe = AsyncMock()
    pipe.__aenter__ = AsyncMock(return_value=pipe)
    pipe.__aexit__ = AsyncMock(return_value=False)
    pipe.hset = MagicMock()
    pipe.execute = AsyncMock()

    store._redis = MagicMock()
    store._redis.pipeline = MagicMock(return_value=pipe)

    records = [_make_record("foo", ["bar"], ["baz"])]
    await store.upsert_deps_batch(records)

    store._redis.pipeline.assert_called_once_with(transaction=False)
    pipe.hset.assert_called_once()
    pipe.execute.assert_called_once()


async def test_multiple_records_one_pipeline_execute() -> None:
    store = GraphStore.__new__(GraphStore)
    pipe = AsyncMock()
    pipe.__aenter__ = AsyncMock(return_value=pipe)
    pipe.__aexit__ = AsyncMock(return_value=False)
    pipe.hset = MagicMock()
    pipe.execute = AsyncMock()

    store._redis = MagicMock()
    store._redis.pipeline = MagicMock(return_value=pipe)

    records = [
        _make_record("a", ["b"], []),
        _make_record("b", [], ["a"]),
        _make_record("c", ["a", "b"], []),
    ]
    await store.upsert_deps_batch(records)

    store._redis.pipeline.assert_called_once_with(transaction=False)
    assert pipe.hset.call_count == 3
    pipe.execute.assert_called_once()


async def test_hset_payload_format() -> None:
    """Each hset must set 'calls' and 'called_by' as JSON strings."""
    import json

    store = GraphStore.__new__(GraphStore)
    pipe = AsyncMock()
    pipe.__aenter__ = AsyncMock(return_value=pipe)
    pipe.__aexit__ = AsyncMock(return_value=False)
    hset_calls: list[dict] = []

    def capture_hset(key, mapping):
        hset_calls.append({"key": key, "mapping": mapping})

    pipe.hset = MagicMock(side_effect=capture_hset)
    pipe.execute = AsyncMock()

    store._redis = MagicMock()
    store._redis.pipeline = MagicMock(return_value=pipe)

    records = [_make_record("my_func", ["helper", "util"], ["caller"])]
    await store.upsert_deps_batch(records)

    assert len(hset_calls) == 1
    call = hset_calls[0]
    assert call["key"] == "dep:my_func"
    assert json.loads(call["mapping"]["calls"]) == ["helper", "util"]
    assert json.loads(call["mapping"]["called_by"]) == ["caller"]
