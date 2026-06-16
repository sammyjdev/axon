from __future__ import annotations

from types import SimpleNamespace

import pytest

from axon.mcp import server
from axon.router.classifier import TaskType


class _FakeVectorStore:
    def __init__(self, results: list[dict], captured: dict[str, object]) -> None:
        self._results = results
        self._captured = captured

    async def search(self, **kwargs):
        self._captured.update(kwargs)
        return self._results


def _stub_strategy_deps(monkeypatch) -> None:
    """Isolate _select_retrieval_strategy from config/strategy I/O."""
    monkeypatch.setattr(server, "_load_retrieval_profile", lambda: ("free", "auto", ()))
    monkeypatch.setattr(
        server, "select_default_retrieval_strategy", lambda **kw: SimpleNamespace(**kw)
    )


def test_retrieval_strategy_skips_cloud_classifier_when_model_pinned(monkeypatch) -> None:
    """A pinned local completion model must keep retrieval offline: the cloud
    classifier is never invoked (it would route to Groq under the FREE profile)."""
    _stub_strategy_deps(monkeypatch)
    calls: list[str] = []

    def _spy(content, ctx=None):
        calls.append(content)
        return (TaskType.DEEP_REASONING, "cloud")

    monkeypatch.setattr("axon.router.classifier.classify_task_with_source", _spy)
    monkeypatch.setenv("AXON_COMPLETION_MODEL", "ollama/qwen2.5:7b")

    _strategy, task_type, _profile, _mode = server._select_retrieval_strategy("q", "knowledge")

    assert calls == [], "classifier must not be called when the model is pinned"
    assert task_type == TaskType.CODE_ANALYSIS.value


def test_retrieval_strategy_uses_classifier_when_model_not_pinned(monkeypatch) -> None:
    _stub_strategy_deps(monkeypatch)
    calls: list[str] = []

    def _spy(content, ctx=None):
        calls.append(content)
        return (TaskType.DEEP_REASONING, "local")

    monkeypatch.setattr("axon.router.classifier.classify_task_with_source", _spy)
    monkeypatch.delenv("AXON_COMPLETION_MODEL", raising=False)

    _strategy, task_type, _profile, _mode = server._select_retrieval_strategy("q", "knowledge")

    assert calls == ["q"], "classifier should run when no model is pinned"
    assert task_type == TaskType.DEEP_REASONING.value


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
    monkeypatch.setattr(
        server, "_get_embedder", lambda: SimpleNamespace(embed_one=lambda query: [0.1])
    )
    monkeypatch.setattr(
        "axon.router.classifier.classify_task_with_source",
        lambda content, ctx=None: (TaskType.CODE_ANALYSIS, "local"),
    )
    monkeypatch.setattr(
        server,
        "_get_session_store",
        lambda: _FakeSessionStore(nodes=["upsert", "VectorStore", "QdrantClient"]),
    )

    response = await server.search_code(
        query="upsert vector", ctx="knowledge", caller="claude-code"
    )

    assert "trace_id:" in response
    assert captured["top_k"] == 8
    assert "### upsert (python)" in response
    assert "## Context pack" in response
    assert "strategy: balanced" in response
    assert "task_type: CODE_ANALYSIS" in response
    assert "segments: 1" in response
    assert "contexts: knowledge" in response


@pytest.mark.asyncio
async def test_ask_surfaces_context_pack_and_skips_compression_for_minimal_strategy(
    monkeypatch,
) -> None:
    class FakeSessionStore:
        async def init(self) -> None:
            return None

        async def query_subgraph(
            self, node_id: str, depth: int = 2
        ) -> dict[str, object]:
            return {"root": node_id, "nodes": [], "edges": []}

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
    monkeypatch.setattr("axon.context.detector.ContextDetector", FakeDetector)
    monkeypatch.setattr(server, "_get_vector_store", lambda: store)
    monkeypatch.setattr(
        server, "_get_embedder", lambda: SimpleNamespace(embed_one=lambda query: [0.1])
    )
    monkeypatch.setattr(
        "axon.router.classifier.classify_task_with_source",
        lambda content, ctx=None: (TaskType.TRIVIAL_COMPLETION, "local"),
    )
    monkeypatch.setattr(server, "caveman_compress_guarded", fail_caveman)

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
    monkeypatch.setattr(
        server, "_get_embedder", lambda: SimpleNamespace(embed_one=lambda query: [0.1])
    )
    monkeypatch.setattr(
        "axon.router.classifier.classify_task_with_source",
        lambda content, ctx=None: (TaskType.CODE_ANALYSIS, "local"),
    )
    monkeypatch.setattr(server, "_get_session_store", lambda: _FakeSessionStore())

    response = await server.search_code(
        query="upsert vector", ctx="knowledge", caller="claude-code"
    )

    assert "## Staleness" in response
    assert "- upsert stale -> replacement=fresh-hit (newer_record_in_family)" in response


class _FakeSessionStore:
    """SQLite-source-of-truth stand-in exposing the graph reads server uses."""

    def __init__(self, nodes: list[str] | None = None) -> None:
        self._nodes = nodes or []

    async def init(self) -> None:
        return None

    async def query_subgraph(self, node_id: str, depth: int = 2) -> dict[str, object]:
        return {"root": node_id, "nodes": self._nodes, "edges": []}


@pytest.mark.asyncio
async def test_search_code_enriches_from_sqlite_not_redis(monkeypatch) -> None:
    """dec-116 #4: the 'related deps' enrichment reads the SQLite source-of-truth
    (query_subgraph), not the Redis traverse cache."""
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
    monkeypatch.setattr(
        server, "_get_embedder", lambda: SimpleNamespace(embed_one=lambda query: [0.1])
    )
    monkeypatch.setattr(
        "axon.router.classifier.classify_task_with_source",
        lambda content, ctx=None: (TaskType.CODE_ANALYSIS, "local"),
    )
    monkeypatch.setattr(
        server,
        "_get_session_store",
        lambda: _FakeSessionStore(nodes=["upsert", "VectorStore", "QdrantClient"]),
    )

    def _boom(*_args, **_kwargs):
        raise AssertionError("Redis traverse must not be called (dec-116 #4)")

    monkeypatch.setattr(
        server,
        "_get_graph_store",
        lambda: SimpleNamespace(connect=lambda: _async_none(), traverse=_boom),
    )

    response = await server.search_code(
        query="upsert vector", ctx="knowledge", caller="claude-code"
    )

    assert "## Dependencias relacionadas" in response
    assert "Root: upsert" in response
    # neighbors come from SQLite, with the anchor itself excluded
    assert "VectorStore" in response
    assert "QdrantClient" in response


async def _async_none():
    return None
