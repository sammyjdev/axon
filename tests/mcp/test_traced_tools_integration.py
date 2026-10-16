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
