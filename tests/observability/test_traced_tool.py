from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

import pytest

from axon.observability.trace_store import TraceStore
from axon.observability.traced_tool import (
    current_trace_recorder,
    traced_tool,
)


def _store(tmp_path) -> TraceStore:
    return TraceStore(runtime=SimpleNamespace(data_root=tmp_path / "data"))


@pytest.mark.asyncio
async def test_decorator_emits_invoke_and_output_stages_on_success(tmp_path) -> None:
    store = _store(tmp_path)

    @traced_tool(risk="read", name="probe", store=store)
    async def probe(caller: str = "claude-code") -> str:
        return "ok"

    result = await probe(caller="claude-code")

    assert result == "ok"
    records = store.load_all()
    assert len(records) == 2
    invoke, output = records
    assert invoke.trace_id == output.trace_id
    assert invoke.stage == "invoke"
    assert invoke.caller == "mcp.probe"
    assert invoke.payload["risk"] == "read"
    assert invoke.payload["caller"] == "claude-code"
    assert output.stage == "output"
    assert output.payload["ok"] is True
    assert output.payload["latency_ms"] >= 0
    assert output.payload["output_tokens"] >= 0


@pytest.mark.asyncio
async def test_decorator_emits_error_stage_and_reraises_on_exception(tmp_path) -> None:
    store = _store(tmp_path)

    @traced_tool(risk="read", name="boom", store=store)
    async def boom() -> str:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await boom()

    records = store.load_all()
    assert [r.stage for r in records] == ["invoke", "error"]
    err = records[1]
    assert err.payload["ok"] is False
    assert err.payload["error_type"] == "RuntimeError"
    assert err.payload["error_msg"] == "boom"
    assert err.payload["latency_ms"] >= 0


@pytest.mark.asyncio
async def test_decorator_does_not_catch_cancelled_error(tmp_path) -> None:
    store = _store(tmp_path)

    @traced_tool(risk="read", name="cancel", store=store)
    async def cancel() -> str:
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await cancel()

    records = store.load_all()
    # invoke is logged; error stage is NOT (we don't swallow CancelledError)
    assert [r.stage for r in records] == ["invoke"]


@pytest.mark.asyncio
async def test_decorator_isolates_recorder_between_concurrent_calls(tmp_path) -> None:
    store = _store(tmp_path)
    observed: list[str | None] = []

    @traced_tool(risk="read", name="peek", store=store)
    async def peek(tag: str) -> str:
        rec = current_trace_recorder()
        assert rec is not None
        observed.append(rec._trace_id)
        await asyncio.sleep(0.01)
        return tag

    results = await asyncio.gather(*(peek(tag=f"t{i}") for i in range(5)))

    assert results == [f"t{i}" for i in range(5)]
    assert len(set(observed)) == 5  # each call had its own trace_id

    records = store.load_all()
    trace_ids = {r.trace_id for r in records}
    assert len(trace_ids) == 5
    assert len(records) == 10  # invoke+output per call


@pytest.mark.asyncio
async def test_decorator_resets_contextvar_after_call(tmp_path) -> None:
    store = _store(tmp_path)

    @traced_tool(risk="read", name="ping", store=store)
    async def ping() -> str:
        return "pong"

    assert current_trace_recorder() is None
    await ping()
    assert current_trace_recorder() is None


@pytest.mark.asyncio
async def test_decorator_sanitizes_string_args_outside_allowlist(tmp_path) -> None:
    store = _store(tmp_path)
    secret = "conteúdo sensível 12345"
    expected_sha8 = hashlib.sha256(secret.encode("utf-8")).hexdigest()[:8]

    @traced_tool(risk="write", name="capture", store=store)
    async def capture(summary: str) -> str:
        return "captured"

    await capture(summary=secret)

    invoke = store.load_all()[0]
    assert invoke.payload["summary_len"] == len(secret)
    assert invoke.payload["summary_sha8"] == expected_sha8
    # the literal must NOT leak
    assert "summary" not in invoke.payload or invoke.payload.get("summary") != secret
    assert secret not in str(invoke.payload)


@pytest.mark.asyncio
async def test_decorator_passes_allowlisted_scalars_verbatim(tmp_path) -> None:
    store = _store(tmp_path)

    @traced_tool(risk="read", name="alm", store=store)
    async def alm(
        caller: str = "x",
        agent: str = "y",
        repo: str = "z",
        ctx: str | None = "knowledge",
        depth: int = 2,
        language: str | None = "python",
        max_tokens: int = 1200,
        top_k: int = 5,
        token_budget: int = 8000,
    ) -> str:
        return "ok"

    await alm()
    invoke = store.load_all()[0]
    for key, value in {
        "caller": "x",
        "agent": "y",
        "repo": "z",
        "ctx": "knowledge",
        "depth": 2,
        "language": "python",
        "max_tokens": 1200,
        "top_k": 5,
        "token_budget": 8000,
    }.items():
        assert invoke.payload.get(key) == value, key


@pytest.mark.asyncio
async def test_decorator_does_not_crash_on_invalid_kwargs(tmp_path) -> None:
    """Regression: previously UnboundLocalError on `bound` if bind_partial raised."""
    store = _store(tmp_path)

    @traced_tool(risk="read", name="strict", store=store)
    async def strict(query: str) -> str:
        return query

    # extra kwarg should produce the function's TypeError, not an
    # UnboundLocalError from the decorator's own arg-processing.
    with pytest.raises(TypeError):
        await strict(query="x", unknown="y")  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_decorator_marks_truncated_payload(tmp_path) -> None:
    """Payload cap should leave a visible marker so consumers know args were dropped."""
    store = _store(tmp_path)

    # 10 non-allowlisted strings → 20 keys (len + sha8 each); cap=16 → truncated.
    @traced_tool(risk="read", name="many", store=store)
    async def many(
        a: str = "",
        b: str = "",
        c: str = "",
        d: str = "",
        e: str = "",
        f: str = "",
        g: str = "",
        h: str = "",
        i: str = "",
        j: str = "",
    ) -> str:
        return "ok"

    await many(
        a="va", b="vb", c="vc", d="vd", e="ve", f="vf", g="vg", h="vh", i="vi", j="vj"
    )
    invoke = store.load_all()[0]
    assert invoke.payload.get("_truncated") is True


@pytest.mark.asyncio
async def test_decorator_summarizes_list_and_dict_as_len(tmp_path) -> None:
    store = _store(tmp_path)

    @traced_tool(risk="write", name="bulk", store=store)
    async def bulk(files: list[str], payload: dict) -> str:
        return "ok"

    await bulk(files=["a", "b", "c"], payload={"k1": 1, "k2": 2})

    invoke = store.load_all()[0]
    assert invoke.payload["files_len"] == 3
    assert invoke.payload["payload_len"] == 2
