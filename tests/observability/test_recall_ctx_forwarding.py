# tests/observability/test_recall_ctx_forwarding.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from axon.mcp import server


def _chunks_written(tmp_path: Path) -> list[dict]:
    # RuntimeConfig.data_root is engine_root / "data", and engine_root comes
    # from AXON_ENGINE (src/axon/config/runtime.py:148).
    chunks_file = tmp_path / "data" / "recall" / "chunks.jsonl"
    if not chunks_file.exists():
        return []
    return [
        json.loads(line)
        for line in chunks_file.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _assert_no_file_contains(root: Path, target: str) -> None:
    target_bytes = target.encode("utf-8")
    for file_path in root.rglob("*"):
        if file_path.is_file():
            content = file_path.read_bytes()
            assert target_bytes not in content, f"Leaked target text in file {file_path}"


class _DummyEmbedder:
    def embed_one(self, text: str) -> list[float]:
        return [0.0] * 1024


class _DummyVectorStore:
    async def search(self, **kwargs: object) -> list[dict]:
        return []


@pytest.fixture()
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("AXON_ENGINE", str(tmp_path))
    return tmp_path


@pytest.mark.parametrize("ctx", ["WORK", "Work", " work ", "work"])
def test_guard_case_insensitivity(data_root: Path, ctx: str) -> None:
    query = "segredo do cliente sensivel"
    server._record_chunk_recall(
        query=query,
        strategy_name="balanced",
        requested_max_tokens=4000,
        hits=[{"payload": {"content": "x", "file_path": "a.py"}, "score": 0.5}],
        ctx=ctx,
    )
    rows = _chunks_written(data_root)
    assert rows, "the record must still be written"
    assert rows[0]["query"] is None
    serialized = json.dumps(rows[0])
    assert query not in serialized


async def test_retrieve_context_forwards_work_ctx(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "_get_embedder", lambda: _DummyEmbedder())
    monkeypatch.setattr(server, "_get_vector_store", lambda: _DummyVectorStore())

    query = "segredo ultra confidencial do cliente avangrid"
    await server._retrieve_context(
        query=query,
        ctx="work",
        language=None,
        max_depth=1,
        max_nodes=10,
        max_tokens=4000,
    )

    rows = _chunks_written(data_root)
    assert rows, "a chunk telemetry row should have been written"
    assert rows[0]["query"] is None

    _assert_no_file_contains(data_root, query)


async def test_retrieve_context_forwards_normal_ctx(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "_get_embedder", lambda: _DummyEmbedder())
    monkeypatch.setattr(server, "_get_vector_store", lambda: _DummyVectorStore())

    query = "onde os vetores sao removidos"
    await server._retrieve_context(
        query=query,
        ctx="knowledge",
        language=None,
        max_depth=1,
        max_nodes=10,
        max_tokens=4000,
    )

    rows = _chunks_written(data_root)
    assert rows, "a chunk telemetry row should have been written"
    assert rows[0]["query"] == query
