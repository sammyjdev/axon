from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from axon.mcp import server
from axon.observability.recall_telemetry import RecallTelemetryStore
from axon.router.classifier import TaskType


@pytest.fixture(autouse=True)
def _isolate_chunk_telemetry(monkeypatch, tmp_path) -> None:
    telemetry_store = RecallTelemetryStore(runtime=SimpleNamespace(data_root=tmp_path))
    monkeypatch.setattr(server, "RecallTelemetryStore", lambda: telemetry_store)


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


def _sha16(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


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


@pytest.mark.asyncio
async def test_retrieve_context_emits_chunk_telemetry_without_raw_content(
    monkeypatch, tmp_path
) -> None:
    captured: dict[str, object] = {}
    query = "How does recall work?"
    content_a = "private vault content alpha"
    content_b = "private vault content beta"
    store = _FakeVectorStore(
        [
            {
                "score": 0.612,
                "ranking_score": 0.598,
                "payload": {
                    "symbol": "alpha",
                    "language": "python",
                    "file_path": "/tmp/a.py",
                    "content": content_a,
                },
            },
            {
                "score": 0.5,
                "payload": {
                    "symbol": "beta",
                    "language": "python",
                    "file_path": "/tmp/b.py",
                    "content": content_b,
                },
            },
        ],
        captured,
    )
    telemetry_store = RecallTelemetryStore(
        runtime=SimpleNamespace(data_root=tmp_path)
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
    monkeypatch.setattr(server, "RecallTelemetryStore", lambda: telemetry_store)

    response, pack, results = await server._retrieve_context(
        query=query,
        ctx="knowledge",
        language=None,
        max_depth=2,
        max_nodes=25,
        max_tokens=2000,
    )

    assert "### alpha (python)" in response
    assert len(pack.segments) == 2
    assert results is store._results
    line = (tmp_path / "recall" / "chunks.jsonl").read_text(encoding="utf-8")
    assert content_a not in line
    assert content_b not in line
    parsed = json.loads(line)
    assert parsed["query_hash"] == _sha16(query)
    assert parsed["strategy"] == "balanced"
    assert parsed["requested_max_tokens"] == 2000
    assert parsed["chunks"] == [
        {
            "hash": _sha16(content_a),
            "ranking_score": 0.598,
            "score": 0.612,
            "token_estimate": len(content_a) // 4,
        },
        {
            "hash": _sha16(content_b),
            "ranking_score": None,
            "score": 0.5,
            "token_estimate": len(content_b) // 4,
        },
    ]


@pytest.mark.asyncio
async def test_retrieve_context_ignores_chunk_telemetry_failure(monkeypatch) -> None:
    class FailingTelemetryStore:
        def append_chunks(self, _record):
            raise OSError("disk full")

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
        {},
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
    monkeypatch.setattr(server, "RecallTelemetryStore", FailingTelemetryStore)

    response, pack, results = await server._retrieve_context(
        query="upsert vector",
        ctx="knowledge",
        language=None,
        max_depth=2,
        max_nodes=25,
        max_tokens=2000,
    )

    assert "### upsert (python)" in response
    assert len(pack.segments) == 1
    assert len(results) == 1


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
