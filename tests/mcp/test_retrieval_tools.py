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
    monkeypatch.delenv("AXON_RERANK", raising=False)
    telemetry_store = RecallTelemetryStore(runtime=SimpleNamespace(data_root=tmp_path))
    monkeypatch.setattr(server, "RecallTelemetryStore", lambda: telemetry_store)


class _FakeVectorStore:
    def __init__(self, results: list[dict], captured: dict[str, object]) -> None:
        self._results = results
        self._captured = captured

    async def search(self, **kwargs):
        self._captured.update(kwargs)
        return self._results


def _sha16(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def _stub_retrieve_deps(monkeypatch, store, *, nodes: list[str] | None = None) -> None:
    monkeypatch.setattr(server, "_get_vector_store", lambda: store)
    monkeypatch.setattr(
        server, "_get_embedder", lambda: SimpleNamespace(embed_one=lambda query: [0.1])
    )
    monkeypatch.setattr(
        "axon.router.classifier.classify_task_with_source",
        lambda content, ctx=None: (TaskType.CODE_ANALYSIS, "local"),
    )
    monkeypatch.setattr(server, "_get_session_store", lambda: _FakeSessionStore(nodes=nodes))


@pytest.mark.parametrize("completion_model", ["", "ollama/qwen2.5:7b"])
def test_retrieval_strategy_is_completion_model_independent(
    monkeypatch, completion_model: str
) -> None:
    monkeypatch.setattr(server, "_load_retrieval_profile", lambda: ("free", "auto", ()))
    if completion_model:
        monkeypatch.setenv("AXON_COMPLETION_MODEL", completion_model)
    else:
        monkeypatch.delenv("AXON_COMPLETION_MODEL", raising=False)

    strategy, task_type, profile, mode = server._select_retrieval_strategy(
        "compare retrieval strategy", "knowledge"
    )

    assert strategy.name == "balanced"
    assert task_type == TaskType.CODE_ANALYSIS.value
    assert profile == "free"
    assert mode == "auto"


def test_retrieval_strategy_never_calls_the_llm_classifier(monkeypatch) -> None:
    # Picking a retrieval budget must never cost an LLM call: the classifier
    # routes through litellm/cloud (the reason the old env-var skip existed).
    monkeypatch.setattr(server, "_load_retrieval_profile", lambda: ("free", "auto", ()))

    def _boom(content, ctx=None):
        raise AssertionError("classifier must not be called for strategy selection")

    monkeypatch.setattr("axon.router.classifier.classify_task_with_source", _boom)

    strategy, task_type, _profile, _mode = server._select_retrieval_strategy(
        "Compare the trade-offs of three architecture options", "knowledge"
    )

    assert strategy.name == "balanced"
    assert task_type == TaskType.CODE_ANALYSIS.value


def test_pb_retrieval_strategy_never_calls_the_llm_classifier(monkeypatch) -> None:
    from axon.cli import pb

    monkeypatch.setattr(server, "_load_retrieval_profile", lambda: ("free", "auto", ()))

    def _boom(content, ctx=None):
        raise AssertionError("classifier must not be called for strategy selection")

    monkeypatch.setattr("axon.router.classifier.classify_task_with_source", _boom)

    strategy, task_type, _profile, _mode = pb._select_retrieval_strategy(
        "Compare the trade-offs of three architecture options", "knowledge"
    )

    assert strategy.name == "balanced"
    assert task_type == TaskType.CODE_ANALYSIS.value


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
    from axon.context.contracts import DEFAULT_RETRIEVAL_STRATEGIES

    minimal = DEFAULT_RETRIEVAL_STRATEGIES["minimal"]
    monkeypatch.setattr(
        server,
        "_select_retrieval_strategy",
        lambda query, ctx: (minimal, TaskType.TRIVIAL_COMPLETION.value, "free", "auto"),
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

    _stub_retrieve_deps(monkeypatch, store)

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

    _stub_retrieve_deps(monkeypatch, store)
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
            "dedup": "off",
            "file_path": "/tmp/a.py",
            "ranking_score": 0.598,
            "score": 0.612,
            "token_estimate": len(content_a) // 4,
        },
        {
            "hash": _sha16(content_b),
            "dedup": "off",
            "file_path": "/tmp/b.py",
            "ranking_score": None,
            "score": 0.5,
            "token_estimate": len(content_b) // 4,
        },
    ]


@pytest.mark.asyncio
async def test_retrieve_context_rerank_flag_off_keeps_search_shape_and_skips_model(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    store = _FakeVectorStore(
        [
            {
                "score": 0.9,
                "payload": {
                    "symbol": "alpha",
                    "language": "python",
                    "file_path": "/tmp/a.py",
                    "content": "alpha content",
                },
            }
        ],
        captured,
    )

    _stub_retrieve_deps(monkeypatch, store)

    def _boom():
        raise AssertionError("reranker must not load when AXON_RERANK is off")

    monkeypatch.setattr(server, "_get_reranker", _boom, raising=False)

    response, pack, results = await server._retrieve_context(
        query="what changed?",
        ctx="knowledge",
        language=None,
        max_depth=2,
        max_nodes=25,
        max_tokens=2000,
    )

    assert "alpha content" in response
    assert len(pack.segments) == 1
    assert results is store._results
    assert captured["top_k"] == 8
    assert captured["max_nodes"] == 25
    assert captured["max_tokens"] == 2000


@pytest.mark.asyncio
async def test_retrieve_context_reranks_wide_candidates(monkeypatch) -> None:
    class FakeReranker:
        def rerank_pairs(self, pairs):
            assert [pair[1] for pair in pairs] == [
                "alpha content",
                "beta content",
                "gamma content",
            ]
            return [0.1, 0.9, 0.2]

    captured: dict[str, object] = {}
    store = _FakeVectorStore(
        [
            {
                "score": 0.6,
                "payload": {
                    "symbol": "alpha",
                    "language": "python",
                    "file_path": "/tmp/a.py",
                    "content": "alpha content",
                },
            },
            {
                "score": 0.61,
                "payload": {
                    "symbol": "beta",
                    "language": "python",
                    "file_path": "/tmp/b.py",
                    "content": "beta content",
                },
            },
            {
                "score": 0.62,
                "payload": {
                    "symbol": "gamma",
                    "language": "python",
                    "file_path": "/tmp/c.py",
                    "content": "gamma content",
                },
            },
        ],
        captured,
    )

    _stub_retrieve_deps(monkeypatch, store)
    monkeypatch.setenv("AXON_RERANK", "1")
    monkeypatch.setattr(server, "_get_reranker", lambda: FakeReranker(), raising=False)

    response, pack, results = await server._retrieve_context(
        query="what changed?",
        ctx="knowledge",
        language=None,
        max_depth=2,
        max_nodes=25,
        max_tokens=100,
    )

    assert captured["top_k"] == 24
    assert captured["max_nodes"] == 24
    assert captured["max_tokens"] == 400
    assert [hit["payload"]["symbol"] for hit in results] == ["beta", "gamma", "alpha"]
    assert [hit["rerank_score"] for hit in results] == [0.9, 0.2, 0.1]
    assert pack.segments[0].startswith("### beta")
    assert response.index("beta content") < response.index("gamma content")


@pytest.mark.asyncio
async def test_rerank_ties_keep_prior_staleness_order(monkeypatch) -> None:
    class TiedReranker:
        def rerank_pairs(self, pairs):
            return [0.5 for _ in pairs]

    store = _FakeVectorStore(
        [
            {
                "score": 0.6,
                "payload": {
                    "symbol": "first",
                    "language": "python",
                    "file_path": "/tmp/a.py",
                    "content": "first content",
                },
            },
            {
                "score": 0.5,
                "payload": {
                    "symbol": "second",
                    "language": "python",
                    "file_path": "/tmp/b.py",
                    "content": "second content",
                },
            },
        ],
        {},
    )
    _stub_retrieve_deps(monkeypatch, store)
    monkeypatch.setenv("AXON_RERANK", "1")
    monkeypatch.setattr(server, "_get_reranker", lambda: TiedReranker(), raising=False)

    _raw, _pack, hits = await server._retrieve_context(
        query="tie", ctx="knowledge", language=None, max_depth=1, max_nodes=25, max_tokens=1200
    )

    # Equal rerank scores: prior (staleness) order preserved.
    assert [h["payload"]["symbol"] for h in hits] == ["first", "second"]


async def test_retrieve_context_reranker_failure_uses_original_order(monkeypatch) -> None:
    class FailingReranker:
        def rerank_pairs(self, _pairs):
            raise RuntimeError("rerank failed")

    store = _FakeVectorStore(
        [
            {
                "score": 0.6,
                "payload": {
                    "symbol": "alpha",
                    "language": "python",
                    "file_path": "/tmp/a.py",
                    "content": "alpha content",
                },
            },
            {
                "score": 0.61,
                "payload": {
                    "symbol": "beta",
                    "language": "python",
                    "file_path": "/tmp/b.py",
                    "content": "beta content",
                },
            },
        ],
        {},
    )

    _stub_retrieve_deps(monkeypatch, store)
    monkeypatch.setenv("AXON_RERANK", "1")
    monkeypatch.setattr(server, "_get_reranker", lambda: FailingReranker(), raising=False)

    response, pack, results = await server._retrieve_context(
        query="what changed?",
        ctx="knowledge",
        language=None,
        max_depth=2,
        max_nodes=25,
        max_tokens=100,
    )

    assert [hit["payload"]["symbol"] for hit in results] == ["alpha", "beta"]
    assert pack.segments[0].startswith("### alpha")
    assert response.index("alpha content") < response.index("beta content")


@pytest.mark.asyncio
async def test_retrieve_context_chunk_telemetry_includes_rerank_score(
    monkeypatch, tmp_path
) -> None:
    class FakeReranker:
        def rerank_pairs(self, _pairs):
            return [0.1, 0.9]

    store = _FakeVectorStore(
        [
            {
                "score": 0.6,
                "payload": {
                    "symbol": "alpha",
                    "language": "python",
                    "file_path": "/tmp/a.py",
                    "content": "alpha content",
                },
            },
            {
                "score": 0.61,
                "payload": {
                    "symbol": "beta",
                    "language": "python",
                    "file_path": "/tmp/b.py",
                    "content": "beta content",
                },
            },
        ],
        {},
    )
    telemetry_store = RecallTelemetryStore(runtime=SimpleNamespace(data_root=tmp_path))

    _stub_retrieve_deps(monkeypatch, store)
    monkeypatch.setattr(server, "RecallTelemetryStore", lambda: telemetry_store)
    monkeypatch.setenv("AXON_RERANK", "1")
    monkeypatch.setattr(server, "_get_reranker", lambda: FakeReranker(), raising=False)

    await server._retrieve_context(
        query="what changed?",
        ctx="knowledge",
        language=None,
        max_depth=2,
        max_nodes=25,
        max_tokens=100,
    )

    parsed = json.loads((tmp_path / "recall" / "chunks.jsonl").read_text(encoding="utf-8"))
    assert [chunk["hash"] for chunk in parsed["chunks"]] == [
        _sha16("beta content"),
        _sha16("alpha content"),
    ]
    assert [chunk["rerank_score"] for chunk in parsed["chunks"]] == [0.9, 0.1]


@pytest.mark.asyncio
async def test_retrieve_context_truncates_reranker_documents(monkeypatch) -> None:
    captured_pairs = []

    class FakeReranker:
        def rerank_pairs(self, pairs):
            captured_pairs.extend(pairs)
            return [1.0]

    store = _FakeVectorStore(
        [
            {
                "score": 0.6,
                "payload": {
                    "symbol": "alpha",
                    "language": "python",
                    "file_path": "/tmp/a.py",
                    "content": "x" * 1300,
                },
            }
        ],
        {},
    )

    _stub_retrieve_deps(monkeypatch, store)
    monkeypatch.setenv("AXON_RERANK", "1")
    monkeypatch.setattr(server, "_get_reranker", lambda: FakeReranker(), raising=False)

    await server._retrieve_context(
        query="what changed?",
        ctx="knowledge",
        language=None,
        max_depth=2,
        max_nodes=25,
        max_tokens=100,
    )

    assert len(captured_pairs) == 1
    assert len(captured_pairs[0][1]) == 1200


@pytest.mark.asyncio
async def test_retrieve_context_drops_hits_already_covered_by_transcript(
    monkeypatch,
) -> None:
    content_a = "covered transcript chunk"
    content_b = "novel retrieved chunk"
    store = _FakeVectorStore(
        [
            {
                "score": 0.9,
                "payload": {
                    "symbol": "covered",
                    "language": "python",
                    "file_path": "/tmp/covered.py",
                    "content": content_a,
                },
            },
            {
                "score": 0.8,
                "payload": {
                    "symbol": "novel",
                    "language": "python",
                    "file_path": "/tmp/novel.py",
                    "content": content_b,
                },
            },
        ],
        {},
    )

    _stub_retrieve_deps(monkeypatch, store)

    response, pack, _results = await server._retrieve_context(
        query="what changed?",
        ctx="knowledge",
        language=None,
        max_depth=2,
        max_nodes=25,
        max_tokens=2000,
        dedup_against=[content_a],
    )

    assert "covered transcript chunk" not in response
    assert "novel retrieved chunk" in response
    assert len(pack.segments) == 1
    assert "novel" in pack.segments[0]


@pytest.mark.asyncio
async def test_retrieve_context_with_dedup_none_keeps_pre_change_pack(
    monkeypatch,
) -> None:
    store = _FakeVectorStore(
        [
            {
                "score": 0.9,
                "payload": {
                    "symbol": "alpha",
                    "language": "python",
                    "file_path": "/tmp/a.py",
                    "content": "alpha content",
                },
            },
            {
                "score": 0.8,
                "payload": {
                    "symbol": "beta",
                    "language": "python",
                    "file_path": "/tmp/b.py",
                    "content": "beta content",
                },
            },
        ],
        {},
    )

    _stub_retrieve_deps(monkeypatch, store)

    response, pack, results = await server._retrieve_context(
        query="what changed?",
        ctx="knowledge",
        language=None,
        max_depth=2,
        max_nodes=25,
        max_tokens=2000,
        dedup_against=None,
    )

    assert results is store._results
    assert len(pack.segments) == 2
    assert "alpha content" in response
    assert "beta content" in response


@pytest.mark.asyncio
async def test_retrieve_context_dedup_telemetry_marks_kept_and_dropped(
    monkeypatch, tmp_path
) -> None:
    content_a = "covered transcript chunk"
    content_b = "novel retrieved chunk"
    store = _FakeVectorStore(
        [
            {
                "score": 0.9,
                "payload": {
                    "symbol": "covered",
                    "language": "python",
                    "file_path": "/tmp/covered.py",
                    "content": content_a,
                },
            },
            {
                "score": 0.8,
                "payload": {
                    "symbol": "novel",
                    "language": "python",
                    "file_path": "/tmp/novel.py",
                    "content": content_b,
                },
            },
        ],
        {},
    )
    telemetry_store = RecallTelemetryStore(runtime=SimpleNamespace(data_root=tmp_path))

    _stub_retrieve_deps(monkeypatch, store)
    monkeypatch.setattr(server, "RecallTelemetryStore", lambda: telemetry_store)

    await server._retrieve_context(
        query="what changed?",
        ctx="knowledge",
        language=None,
        max_depth=2,
        max_nodes=25,
        max_tokens=2000,
        dedup_against=[content_a],
    )

    parsed = json.loads((tmp_path / "recall" / "chunks.jsonl").read_text(encoding="utf-8"))
    assert [chunk["dedup"] for chunk in parsed["chunks"]] == ["dropped", "kept"]


@pytest.mark.asyncio
async def test_retrieve_context_all_covered_hits_pack_empty(monkeypatch) -> None:
    content = "covered transcript chunk"
    store = _FakeVectorStore(
        [
            {
                "score": 0.9,
                "payload": {
                    "symbol": "covered",
                    "language": "python",
                    "file_path": "/tmp/covered.py",
                    "content": content,
                },
            }
        ],
        {},
    )

    _stub_retrieve_deps(monkeypatch, store, nodes=["leak"])

    response, pack, _results = await server._retrieve_context(
        query="what changed?",
        ctx="knowledge",
        language=None,
        max_depth=2,
        max_nodes=25,
        max_tokens=2000,
        dedup_against=[content],
    )

    assert pack.segments == ()
    assert "covered transcript chunk" not in response
    assert "leak" not in response


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
