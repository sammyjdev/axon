from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from axon.mcp import server


def _hits() -> list[dict]:
    return [
        {"score": 0.9, "payload": {"content": "irrelevant text", "file_path": "z.py"}},
        {
            "score": 0.8,
            "payload": {"content": "delete_by_file removes vectors", "file_path": "a.py"},
        },
    ]


class _DummyEmbedder:
    def embed_one(self, text: str) -> list[float]:
        return [0.0] * 1024


class _DummyVectorStore:
    async def search(self, **kwargs: object) -> list[dict]:
        return [
            {
                "score": 0.9,
                "payload": {"content": "alpha content", "file_path": "a.py"},
            }
        ]


def test_rerank_returns_a_reason_when_the_model_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rerank that did not run must be distinguishable from one that did.

    This is the judge-is-dead defect: except Exception returned the input order
    and the pack looked identical to a reranked one.
    """
    def boom() -> object:
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(server, "_get_reranker", boom)
    hits, reason = server._rerank_hits("query", _hits())
    assert reason is not None
    assert [h["score"] for h in hits] == [0.9, 0.8], "input order must survive"


def test_rerank_returns_no_reason_when_it_ran(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeReranker:
        def rerank_pairs(self, pairs: list[tuple[str, str]]) -> list[float]:
            return [0.1, 9.9]

    monkeypatch.setattr(server, "_get_reranker", lambda: FakeReranker())
    hits, reason = server._rerank_hits("query", _hits())
    assert reason is None
    assert hits[0]["payload"]["file_path"] == "a.py", "the reranker's order must win"


def test_rerank_on_empty_hits_is_a_no_op() -> None:
    hits, reason = server._rerank_hits("query", [])
    assert hits == []
    assert reason is None


def test_rerank_is_enabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AXON_RERANK", raising=False)
    assert server._rerank_enabled() is True


def test_rerank_can_be_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AXON_RERANK", "0")
    assert server._rerank_enabled() is False


def test_loader_gives_up_instead_of_hanging(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stalled HF download must degrade, not pin the MCP server forever."""
    monkeypatch.setattr(server, "_reranker", None, raising=False)
    monkeypatch.setattr(server, "_RERANK_LOAD_TIMEOUT_S", 0.2)

    class SlowEncoder:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            time.sleep(5)

    monkeypatch.setattr(server, "_text_cross_encoder_cls", lambda: SlowEncoder)
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        server._get_reranker()
    elapsed = time.monotonic() - start
    assert elapsed < 2.0, f"loader took {elapsed:.2f}s, expected < 2s"


@pytest.mark.asyncio
async def test_retrieve_context_records_rerank_failure_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AXON_ENGINE", str(tmp_path))
    monkeypatch.setenv("AXON_RERANK", "1")
    monkeypatch.setattr(server, "_get_embedder", lambda: _DummyEmbedder())
    monkeypatch.setattr(server, "_get_vector_store", lambda: _DummyVectorStore())

    def boom() -> object:
        raise RuntimeError("model crashed")

    monkeypatch.setattr(server, "_get_reranker", boom)

    await server._retrieve_context(
        query="test query",
        ctx="knowledge",
        language=None,
        max_depth=1,
        max_nodes=10,
        max_tokens=4000,
    )

    chunks_file = tmp_path / "data" / "recall" / "chunks.jsonl"
    assert chunks_file.exists(), "chunks telemetry file must exist"
    rows = [
        json.loads(line)
        for line in chunks_file.read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert len(rows) == 1
    assert rows[0]["rerank"] is not None
    assert "RuntimeError" in rows[0]["rerank"]
    assert "model crashed" in rows[0]["rerank"]


def test_reranker_load_failure_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed load must not be re-attempted on subsequent calls in the same process."""
    monkeypatch.setattr(server, "_reranker", None, raising=False)
    monkeypatch.setattr(server, "_reranker_load_failed", False, raising=False)
    monkeypatch.setattr(server, "_RERANK_LOAD_TIMEOUT_S", 0.1)

    construction_count = 0

    class SlowEncoder:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            nonlocal construction_count
            construction_count += 1
            time.sleep(2)

    monkeypatch.setattr(server, "_text_cross_encoder_cls", lambda: SlowEncoder)

    with pytest.raises(TimeoutError):
        server._get_reranker()

    assert construction_count == 1

    start = time.monotonic()
    with pytest.raises(RuntimeError):
        server._get_reranker()
    elapsed = time.monotonic() - start

    assert elapsed < 0.05, f"second call took {elapsed:.4f}s, expected < 0.05s"
    assert construction_count == 1, "second call must not construct another encoder"


@pytest.mark.asyncio
async def test_retrieve_context_records_disabled_rerank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AXON_ENGINE", str(tmp_path))
    monkeypatch.setenv("AXON_RERANK", "0")
    monkeypatch.setattr(server, "_get_embedder", lambda: _DummyEmbedder())
    monkeypatch.setattr(server, "_get_vector_store", lambda: _DummyVectorStore())

    await server._retrieve_context(
        query="test query",
        ctx="knowledge",
        language=None,
        max_depth=1,
        max_nodes=10,
        max_tokens=4000,
    )

    chunks_file = tmp_path / "data" / "recall" / "chunks.jsonl"
    assert chunks_file.exists(), "chunks telemetry file must exist"
    rows = [
        json.loads(line)
        for line in chunks_file.read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert len(rows) == 1
    assert rows[0]["rerank"] == "disabled"

