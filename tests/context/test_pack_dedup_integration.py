# tests/context/test_pack_dedup_integration.py
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from axon.mcp import server


class _DummyEmbedder:
    def embed_one(self, text: str) -> list[float]:
        return [0.0] * 1024


class _DummyVectorStore:
    def __init__(self, hits: list[dict]) -> None:
        self._hits = hits

    async def search(self, **kwargs: object) -> list[dict]:
        return self._hits


def _hit(content: str, file_path: str, score: float = 0.5) -> dict:
    return {"score": score, "payload": {"content": content, "file_path": file_path}}


@pytest.fixture()
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("AXON_ENGINE", str(tmp_path))
    return tmp_path


async def test_retrieve_context_drops_identical_content_across_files(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hits = [
        _hit("duplicate content", "a.py", 0.9),
        _hit("duplicate content", "b.py", 0.8),
    ]
    monkeypatch.setattr(server, "_get_embedder", lambda: _DummyEmbedder())
    monkeypatch.setattr(server, "_get_vector_store", lambda: _DummyVectorStore(hits))

    _response, _pack, results = await server._retrieve_context(
        query="test query",
        ctx="knowledge",
        language=None,
        max_depth=1,
        max_nodes=10,
        max_tokens=4000,
    )

    assert len(results) == 1
    assert results[0]["payload"]["file_path"] == "a.py"


async def test_retrieve_context_caps_at_most_two_chunks_per_file(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hits = [_hit(f"content {i}", "a.py", 0.9 - (i * 0.1)) for i in range(5)]
    monkeypatch.setattr(server, "_get_embedder", lambda: _DummyEmbedder())
    monkeypatch.setattr(server, "_get_vector_store", lambda: _DummyVectorStore(hits))

    _response, _pack, results = await server._retrieve_context(
        query="test query",
        ctx="knowledge",
        language=None,
        max_depth=1,
        max_nodes=10,
        max_tokens=4000,
    )

    assert len(results) == 2
    assert [r["payload"]["content"] for r in results] == ["content 0", "content 1"]


async def test_retrieve_context_preserves_store_identity_when_no_duplicates(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hits = [
        _hit("content a", "a.py", 0.9),
        _hit("content b", "b.py", 0.8),
    ]
    monkeypatch.setattr(server, "_get_embedder", lambda: _DummyEmbedder())
    monkeypatch.setattr(server, "_get_vector_store", lambda: _DummyVectorStore(hits))

    _response, _pack, results = await server._retrieve_context(
        query="test query",
        ctx="knowledge",
        language=None,
        max_depth=1,
        max_nodes=10,
        max_tokens=4000,
    )

    assert len(results) == 2
    assert results is hits

