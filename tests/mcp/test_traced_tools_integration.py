from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from types import SimpleNamespace

import pytest

from axon.mcp import server
from axon.observability.trace_store import TraceStore
from axon.store.session_store import SessionStore


@pytest.fixture
def trace_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TraceStore:
    ts = TraceStore(runtime=SimpleNamespace(data_root=tmp_path / "trace_data"))
    monkeypatch.setattr(server, "_TRACE_STORE", ts)
    return ts


@pytest.fixture
async def session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[SessionStore, None]:
    s = SessionStore(db_path=tmp_path / "axon.db")
    await s.init()
    monkeypatch.setattr(server, "_get_session_store", lambda: s)
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_axon_capture_emits_invoke_and_output_records(
    trace_store: TraceStore,
    session: SessionStore,
) -> None:
    await server.axon_capture(summary="trace me", repo="axon", agent="codex")

    records = trace_store.load_all()
    stages = [r.stage for r in records]
    assert stages == ["invoke", "policy", "output"]
    assert records[0].caller == "mcp.axon_capture"
    assert records[0].payload["risk"] == "write"
    # summary text must NOT leak — only len/sha8
    assert "summary_len" in records[0].payload
    assert "summary_sha8" in records[0].payload
    assert records[0].payload.get("summary") != "trace me"
    assert records[-1].payload["ok"] is True


@pytest.mark.asyncio
async def test_failing_tool_emits_error_record_and_reraises(
    trace_store: TraceStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenStore:
        async def init(self) -> None:
            raise RuntimeError("db unavailable")

    monkeypatch.setattr(server, "_get_session_store", lambda: BrokenStore())

    with pytest.raises(RuntimeError, match="db unavailable"):
        await server.axon_get_context(repo="axon")

    records = trace_store.load_all()
    assert [r.stage for r in records] == ["invoke", "error"]
    assert records[1].payload["error_type"] == "RuntimeError"
    assert records[1].payload["error_msg"] == "db unavailable"


@pytest.mark.asyncio
async def test_search_code_consolidates_trace_id(
    trace_store: TraceStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from axon.router.classifier import TaskType

    captured: dict[str, object] = {}

    class _FakeVectorStore:
        async def search(self, **kwargs):
            captured.update(kwargs)
            return [
                {
                    "score": 0.9,
                    "payload": {
                        "symbol": "x",
                        "language": "python",
                        "file_path": "/tmp/x.py",
                        "content": "x=1",
                    },
                }
            ]

    monkeypatch.setattr(server, "_get_vector_store", lambda: _FakeVectorStore())
    monkeypatch.setattr(
        server, "_get_embedder", lambda: SimpleNamespace(embed_one=lambda q: [0.1])
    )
    monkeypatch.setattr(
        "axon.router.classifier.classify_task_with_source",
        lambda content, ctx=None: (TaskType.CODE_ANALYSIS, "local"),
    )

    async def _none():
        return None

    async def _traverse(symbol, max_depth, max_nodes):
        return {"root": symbol, "nodes": []}

    monkeypatch.setattr(
        server,
        "_get_graph_store",
        lambda: SimpleNamespace(connect=lambda: _none(), traverse=_traverse),
    )

    response = await server.search_code(query="x", ctx="knowledge")

    # extract the trace_id surfaced in the response header
    first_line = response.splitlines()[0]
    assert first_line.startswith("trace_id: ")
    response_trace_id = first_line.removeprefix("trace_id: ").strip()

    records = trace_store.load_all()
    assert len({r.trace_id for r in records}) == 1
    assert records[0].trace_id == response_trace_id
    # invoke + retrieval + output, all sharing the same trace_id
    stages = [r.stage for r in records]
    assert stages[0] == "invoke"
    assert "retrieval" in stages
    assert stages[-1] == "output"
