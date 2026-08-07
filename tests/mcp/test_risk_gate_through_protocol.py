"""The risk gate must survive the mcp.tool() wrapper, not just exist beside it.

`@mcp.tool()` and `@traced_tool(risk=...)` are stacked on all 22 tools.
traced_tool pulls `ctx` out of the call with inspect.signature + bind_partial,
and its except-TypeError branch returns ctx=None silently. So if the SDK ever
changes how it invokes the wrapped function, the gate keeps running but starts
deciding on "no context" instead of "work" - and that direction ALLOWS rather
than denies.

Existing tests call the tool functions directly, which never exercises the
wrapper. These go through call_tool, which is the only path that does.
See dec-109 / ADR-013.
"""
from __future__ import annotations

import pytest

from axon.mcp import server
from axon.observability.traced_tool import traced_tool
from axon.policy.core import PolicyRegistry


async def test_ctx_reaches_traced_tool_through_call_tool() -> None:
    """The failure this guards is silent: ctx=None widens the gate."""
    seen: dict[str, object] = {}

    @server.mcp.tool()
    @traced_tool(risk="read")
    async def _ctx_probe(ctx: str | None = None) -> str:
        seen["ctx"] = ctx
        return "ok"

    try:
        await server.mcp.call_tool("_ctx_probe", {"ctx": "work"})
    finally:
        remove = getattr(server.mcp, "remove_tool", None)
        if remove:
            remove("_ctx_probe")

    assert seen.get("ctx") == "work", (
        f"ctx arrived as {seen.get('ctx')!r}; the gate would decide on the wrong context"
    )


@pytest.mark.parametrize(
    "risk,ctx,expected_allowed,expected_reason",
    [
        ("destructive", "personal", False, "DENY_DESTRUCTIVE_NO_CONSENT"),
        ("write", "work", False, "DENY_RESTRICTED_TOOL_WRITE"),
        ("destructive", "work", False, "DENY_RESTRICTED_TOOL_WRITE"),
        ("read", "work", True, "ALLOW_PUBLIC"),
        ("write", "personal", True, "ALLOW_PUBLIC"),
    ],
)
def test_gate_decisions(monkeypatch, risk, ctx, expected_allowed, expected_reason) -> None:
    """Pins the decision table itself, so a policy edit has to be deliberate."""
    monkeypatch.delenv("AXON_ALLOW_DESTRUCTIVE", raising=False)

    decision = PolicyRegistry().decide_tool_action(risk=risk, ctx=ctx)

    assert decision.allowed is expected_allowed
    assert decision.reason_code == expected_reason


def test_destructive_is_allowed_only_with_explicit_consent(monkeypatch) -> None:
    monkeypatch.setenv("AXON_ALLOW_DESTRUCTIVE", "1")

    decision = PolicyRegistry().decide_tool_action(risk="destructive", ctx="personal")

    assert decision.allowed is True
