"""Every MCP tool failure should leave a FailureRecord.

failure_record has been empty since it was created: its only writer was
ExpansionService, which nothing in src/ ever instantiated. The trace already
passes through one `except Exception` for every tool, so that is where the
record belongs - one seam instead of a call site per tool.
"""
from importlib import import_module
from types import SimpleNamespace

import pytest

from axon.observability.trace_store import TraceStore
from axon.observability.traced_tool import traced_tool

# `axon.observability` exports a function named `traced_tool`, which shadows
# the submodule of the same name under both `import ... as` and monkeypatch's
# string resolution. import_module returns the module itself.
traced_module = import_module("axon.observability.traced_tool")


@pytest.fixture
def trace_store(tmp_path) -> TraceStore:
    return TraceStore(runtime=SimpleNamespace(data_root=tmp_path / "data"))


class _Sink:
    def __init__(self, *, explode: bool = False) -> None:
        self.saved: list = []
        self._explode = explode
        self.inited = False

    async def init(self) -> None:
        # Part of the store contract as of the fix for the cold-schema defect:
        # init() is what runs CREATE TABLE IF NOT EXISTS, and _record_tool_failure
        # now calls it. A fake that omits it lets the real path fail while the
        # test stays green - which is exactly how that defect shipped.
        self.inited = True

    async def save_failure(self, record) -> None:
        if self._explode:
            raise RuntimeError("failure store is down")
        self.saved.append(record)


@pytest.mark.asyncio
async def test_a_failing_tool_records_the_failure(monkeypatch, trace_store):
    sink = _Sink()
    monkeypatch.setattr(traced_module, "_resolve_failure_store", lambda: sink)

    @traced_tool(risk="read", store=trace_store)
    async def boom(ctx: str = "personal") -> str:
        raise ValueError("index is corrupt")

    with pytest.raises(ValueError):
        await boom()

    assert len(sink.saved) == 1
    record = sink.saved[0]
    assert record.operation == "boom"
    assert "index is corrupt" in record.error_message
    assert "ValueError" in record.probable_cause


@pytest.mark.asyncio
async def test_a_succeeding_tool_records_nothing(monkeypatch, trace_store):
    sink = _Sink()
    monkeypatch.setattr(traced_module, "_resolve_failure_store", lambda: sink)

    @traced_tool(risk="read", store=trace_store)
    async def fine(ctx: str = "personal") -> str:
        return "ok"

    await fine()

    assert sink.saved == []


@pytest.mark.asyncio
async def test_a_broken_failure_store_never_masks_the_original_error(monkeypatch, trace_store):
    """Diagnostics must not become a second failure mode: if recording the
    failure fails, the caller still sees the failure that actually happened."""
    monkeypatch.setattr(traced_module, "_resolve_failure_store", lambda: _Sink(explode=True))

    @traced_tool(risk="read", store=trace_store)
    async def boom(ctx: str = "personal") -> str:
        raise ValueError("the real problem")

    with pytest.raises(ValueError, match="the real problem"):
        await boom()
