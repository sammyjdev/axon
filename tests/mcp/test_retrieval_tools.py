from __future__ import annotations

from types import SimpleNamespace

import pytest

from prometheus.mcp import server
from prometheus.router.classifier import TaskType


class _FakeVectorStore:
    def __init__(self, results: list[dict], captured: dict[str, object]) -> None:
        self._results = results
        self._captured = captured

    async def search(self, **kwargs):
        self._captured.update(kwargs)
        return self._results


@pytest.mark.asyncio
async def test_search_code_applies_strategy_budget_and_returns_context_pack(monkeypatch) -> None:
    captured: dict[str, object] = {}
    store = _FakeVectorStore(
        [
            {
                "score": 0.91,
                "payload": {
                    "symbol": "upsert",
                    "language": "python",
                    "file_path": "/tmp/vector_store.py",
                    "content": "async def upsert(self, chunk): ...",
                },
            }
        ],
        captured,
    )

    monkeypatch.setattr(server, "_get_vector_store", lambda: store)
    monkeypatch.setattr(server, "_get_embedder", lambda: SimpleNamespace(embed_one=lambda query: [0.1]))
    monkeypatch.setattr(
        "prometheus.router.classifier.classify_task_with_source",
        lambda content, ctx=None: (TaskType.CODE_ANALYSIS, "local"),
    )
    monkeypatch.setattr(
        server,
        "_get_graph_store",
        lambda: SimpleNamespace(
            connect=lambda: _async_none(),
            traverse=lambda symbol, max_depth, max_nodes: _async_result(
                {"root": symbol, "nodes": ["VectorStore", "QdrantClient"]}
            ),
        ),
    )

    response = await server.search_code(query="upsert vector", ctx="knowledge", caller="claude-code")

    assert "trace_id:" in response
    assert captured["top_k"] == 8
    assert "### upsert (python)" in response
    assert "## Context pack" in response
    assert "strategy: balanced" in response
    assert "task_type: CODE_ANALYSIS" in response
    assert "segments: 1" in response
    assert "contexts: knowledge" in response


@pytest.mark.asyncio
async def test_ask_surfaces_context_pack_and_skips_compression_for_minimal_strategy(monkeypatch) -> None:
    class FakeSessionStore:
        async def init(self) -> None:
            return None

    class FakeDetector:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def detect(self, *_args, **_kwargs):
            return SimpleNamespace(context="knowledge", display="[knowledge 50%]")

    captured: dict[str, object] = {}
    store = _FakeVectorStore(
        [
            {
                "score": 0.88,
                "payload": {
                    "symbol": "upsert",
                    "language": "python",
                    "file_path": "/tmp/vector_store.py",
                    "content": "async def upsert(self, chunk): ...",
                },
            }
        ],
        captured,
    )

    async def fail_caveman(*_args, **_kwargs):
        raise AssertionError("compression should be skipped for minimal strategy")

    monkeypatch.setattr(server, "_get_session_store", lambda: FakeSessionStore())
    monkeypatch.setattr("prometheus.context.detector.ContextDetector", FakeDetector)
    monkeypatch.setattr(server, "_get_vector_store", lambda: store)
    monkeypatch.setattr(server, "_get_embedder", lambda: SimpleNamespace(embed_one=lambda query: [0.1]))
    monkeypatch.setattr(
        "prometheus.router.classifier.classify_task_with_source",
        lambda content, ctx=None: (TaskType.TRIVIAL_COMPLETION, "local"),
    )
    monkeypatch.setattr(server, "caveman_compress_guarded", fail_caveman)
    monkeypatch.setattr(
        server,
        "_get_graph_store",
        lambda: SimpleNamespace(
            connect=lambda: _async_none(),
            traverse=lambda symbol, max_depth, max_nodes: _async_result({"root": symbol, "nodes": []}),
        ),
    )

    response = await server.ask(query="upsert?", ctx="knowledge", caller="claude-code")

    assert "trace_id:" in response
    assert captured["top_k"] == 4
    assert "## compression" in response
    assert "engine: disabled" in response
    assert "## Context pack" in response
    assert "strategy: minimal" in response
    assert "task_type: TRIVIAL_COMPLETION" in response


@pytest.mark.asyncio
async def test_search_code_surfaces_staleness_notes(monkeypatch) -> None:
    captured: dict[str, object] = {}
    store = _FakeVectorStore(
        [
            {
                "score": 0.91,
                "payload": {
                    "symbol": "upsert",
                    "language": "python",
                    "file_path": "/tmp/vector_store.py",
                    "content": "async def upsert(self, chunk): ...",
                },
                "staleness": {
                    "score": 1.0,
                    "is_stale": True,
                    "reasons": ["age_exceeds_stale_window"],
                    "replacement_family": "runbooks/search.md",
                    "replacement_id": "fresh-hit",
                    "replacement_reason": "newer_record_in_family",
                },
            }
        ],
        captured,
    )

    monkeypatch.setattr(server, "_get_vector_store", lambda: store)
    monkeypatch.setattr(server, "_get_embedder", lambda: SimpleNamespace(embed_one=lambda query: [0.1]))
    monkeypatch.setattr(
        "prometheus.router.classifier.classify_task_with_source",
        lambda content, ctx=None: (TaskType.CODE_ANALYSIS, "local"),
    )
    monkeypatch.setattr(
        server,
        "_get_graph_store",
        lambda: SimpleNamespace(
            connect=lambda: _async_none(),
            traverse=lambda symbol, max_depth, max_nodes: _async_result(
                {"root": symbol, "nodes": []}
            ),
        ),
    )

    response = await server.search_code(query="upsert vector", ctx="knowledge", caller="claude-code")

    assert "## Staleness" in response
    assert "- upsert stale -> replacement=fresh-hit (newer_record_in_family)" in response


async def _async_none():
    return None


async def _async_result(value):
    return value
