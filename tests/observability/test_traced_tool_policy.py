from __future__ import annotations

import logging
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


@pytest.mark.parametrize("value", ["true", "TRUE", "yes", "Yes", "on", "1"])
@pytest.mark.asyncio
async def test_destructive_consent_accepts_truthy_variants(
    tmp_path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("AXON_ALLOW_DESTRUCTIVE", value)
    store = _store(tmp_path)

    @traced_tool(risk="destructive", name="d_truthy", store=store)
    async def d_truthy() -> str:
        return "ok"

    assert await d_truthy() == "ok"


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off"])
@pytest.mark.asyncio
async def test_destructive_consent_rejects_falsy_variants(
    tmp_path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("AXON_ALLOW_DESTRUCTIVE", value)
    store = _store(tmp_path)

    @traced_tool(risk="destructive", name="d_falsy", store=store)
    async def d_falsy() -> str:
        return "ok"

    with pytest.raises(PolicyDenied):
        await d_falsy()


@pytest.mark.asyncio
async def test_write_denied_for_restricted_ctx(tmp_path) -> None:
    store = _store(tmp_path)

    @traced_tool(risk="write", name="w_restricted", store=store)
    async def w_restricted(ctx: str | None = None) -> str:
        return "wrote"

    with pytest.raises(PolicyDenied) as excinfo:
        await w_restricted(ctx="work")

    assert excinfo.value.decision.reason_code.value == "DENY_RESTRICTED_TOOL_WRITE"


@pytest.mark.asyncio
async def test_destructive_emits_compliance_event(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("AXON_ALLOW_DESTRUCTIVE", "1")
    store = _store(tmp_path)

    @traced_tool(risk="destructive", name="d_audit", store=store)
    async def d_audit() -> str:
        return "ok"

    with caplog.at_level(logging.INFO, logger="axon.observability.compliance"):
        await d_audit()

    assert any(
        "compliance_event=" in rec.message and "DENY_DESTRUCTIVE_NO_CONSENT" not in rec.message
        for rec in caplog.records
    )


@pytest.mark.asyncio
async def test_denied_destructive_emits_compliance_event(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("AXON_ALLOW_DESTRUCTIVE", raising=False)
    store = _store(tmp_path)

    @traced_tool(risk="destructive", name="d_audit_deny", store=store)
    async def d_audit_deny() -> str:
        return "ok"

    with caplog.at_level(logging.INFO, logger="axon.observability.compliance"):
        with pytest.raises(PolicyDenied):
            await d_audit_deny()

    assert any(
        "compliance_event=" in rec.message and "DENY_DESTRUCTIVE_NO_CONSENT" in rec.message
        for rec in caplog.records
    )


@pytest.mark.asyncio
async def test_non_string_ctx_does_not_silently_become_none(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    store = _store(tmp_path)

    @traced_tool(risk="write", name="w_enum", store=store)
    async def w_enum(ctx: object = None) -> str:
        return "ok"

    class _CtxObj:
        def __str__(self) -> str:
            return "knowledge"

    with caplog.at_level(logging.WARNING, logger="axon.observability.traced_tool"):
        await w_enum(ctx=_CtxObj())

    policy_record = next(r for r in store.load_all() if r.stage == "policy")
    assert policy_record.ctx == "knowledge"
    assert any("non-string ctx" in rec.message.lower() for rec in caplog.records)
