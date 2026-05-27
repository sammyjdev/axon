from __future__ import annotations

from types import SimpleNamespace

import pytest

from axon.observability.trace_store import TraceStore
from axon.observability.traced_tool import traced_tool
from axon.policy.core import PolicyDenied


def _store(tmp_path) -> TraceStore:
    return TraceStore(runtime=SimpleNamespace(data_root=tmp_path / "data"))


@pytest.mark.asyncio
async def test_read_tool_does_not_emit_policy_stage(tmp_path) -> None:
    store = _store(tmp_path)

    @traced_tool(risk="read", name="r", store=store)
    async def r() -> str:
        return "ok"

    await r()
    stages = [rec.stage for rec in store.load_all()]
    assert "policy" not in stages
    assert stages == ["invoke", "output"]


@pytest.mark.asyncio
async def test_write_tool_emits_policy_stage_and_proceeds(tmp_path) -> None:
    store = _store(tmp_path)

    @traced_tool(risk="write", name="w", store=store)
    async def w() -> str:
        return "ok"

    result = await w()

    assert result == "ok"
    stages = [rec.stage for rec in store.load_all()]
    assert stages == ["invoke", "policy", "output"]
    policy_record = store.load_all()[1]
    assert policy_record.payload["allowed"] is True
    assert policy_record.payload["reason_code"] in {"ALLOW_PUBLIC", "ALLOW_LOCAL"}


@pytest.mark.asyncio
async def test_destructive_tool_denies_without_consent_env(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AXON_ALLOW_DESTRUCTIVE", raising=False)
    store = _store(tmp_path)

    @traced_tool(risk="destructive", name="d", store=store)
    async def d() -> str:
        return "should not run"

    with pytest.raises(PolicyDenied) as excinfo:
        await d()

    assert excinfo.value.decision.reason_code.value == "DENY_DESTRUCTIVE_NO_CONSENT"
    stages = [rec.stage for rec in store.load_all()]
    assert stages == ["invoke", "policy", "error"]
    err = store.load_all()[2]
    assert err.payload["error_type"] == "PolicyDenied"
    assert err.payload.get("reason_code") == "DENY_DESTRUCTIVE_NO_CONSENT"


@pytest.mark.asyncio
async def test_destructive_tool_allowed_with_consent_env(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AXON_ALLOW_DESTRUCTIVE", "1")
    store = _store(tmp_path)

    @traced_tool(risk="destructive", name="d2", store=store)
    async def d2() -> str:
        return "destroyed"

    result = await d2()
    assert result == "destroyed"
    stages = [rec.stage for rec in store.load_all()]
    assert stages == ["invoke", "policy", "output"]
