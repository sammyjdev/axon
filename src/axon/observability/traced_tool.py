from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from functools import wraps
from typing import Any, Literal

from axon.observability.trace_store import TracePayload, TraceRecorder, TraceStore

logger = logging.getLogger(__name__)

RiskClass = Literal["read", "write", "destructive"]

# Args whose values are safe to log verbatim (low cardinality, no PII).
_ARG_VALUE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "risk",
        "caller",
        "agent",
        "repo",
        "ctx",
        "event_type",
        "depth",
        "language",
        "max_depth",
        "max_nodes",
        "max_tokens",
        "top_k",
        "token_budget",
        "rtk_max_tokens",
        "to_agent",
        "project",
        "symbol",
    }
)

_MAX_PAYLOAD_KEYS = 16

_CURRENT_RECORDER: ContextVar[TraceRecorder | None] = ContextVar(
    "axon_current_trace_recorder", default=None
)


def current_trace_recorder() -> TraceRecorder | None:
    return _CURRENT_RECORDER.get()


def _sha8(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def _summarize_args(bound: inspect.BoundArguments) -> TracePayload:
    payload: TracePayload = {}
    truncated = False
    items = list(bound.arguments.items())
    for idx, (name, value) in enumerate(items):
        if len(payload) >= _MAX_PAYLOAD_KEYS - 1:
            # leave room for the _truncated marker and stop iterating;
            # accounting for remaining args being silently dropped.
            if idx < len(items):
                truncated = True
            break
        if value is None:
            payload[name] = None
            continue
        if isinstance(value, bool) or isinstance(value, (int, float)):
            payload[name] = value
            continue
        if isinstance(value, str):
            if name in _ARG_VALUE_ALLOWLIST:
                payload[name] = value
            else:
                payload[f"{name}_len"] = len(value)
                payload[f"{name}_sha8"] = _sha8(value)
            continue
        if isinstance(value, (list, tuple, set)):
            payload[f"{name}_len"] = len(value)
            continue
        if isinstance(value, dict):
            payload[f"{name}_len"] = len(value)
            continue
        # fallback: only record type
        payload[f"{name}_type"] = type(value).__name__
    if truncated:
        payload["_truncated"] = True
    return payload


def _process_arguments(
    sig: inspect.Signature, args: tuple, kwargs: dict
) -> tuple[TracePayload, Any]:
    """Bind args, summarize them, and extract the ctx value.

    Returns (arg_payload, raw_ctx_value). Raw ctx is returned unprocessed
    so the caller can run its own coercion / warning. If bind_partial
    rejects the call (e.g. unexpected kwarg), we return empty payload and
    None ctx — the wrapped function will then raise the real TypeError
    when the caller forwards the args to it.
    """
    try:
        bound = sig.bind_partial(*args, **kwargs)
        bound.apply_defaults()
    except TypeError:
        return {}, None
    return _summarize_args(bound), bound.arguments.get("ctx")


def _estimate_tokens(result: Any) -> int:
    if result is None:
        return 0
    if isinstance(result, str):
        return len(result) // 4
    try:
        return len(str(result)) // 4
    except Exception:
        return 0


def _coerce_ctx(value: Any, *, tool_name: str) -> str | None:
    if value is None or isinstance(value, str):
        return value
    coerced = str(value)
    logger.warning(
        "non-string ctx %r in %s coerced to %r — pass an explicit string ctx",
        type(value).__name__,
        tool_name,
        coerced,
    )
    return coerced


def _resolve_store(store: TraceStore | None) -> TraceStore:
    if store is not None:
        return store
    # Imported lazily to avoid a circular import at module load time.
    from axon.mcp import server as _server

    return _server._TRACE_STORE


def _resolve_policy():
    from axon.policy.core import PolicyRegistry

    # New instance each call to honour any test-level env mutation
    # (e.g. AXON_ALLOW_DESTRUCTIVE) without state caching.
    return PolicyRegistry()


def traced_tool(
    *,
    risk: RiskClass,
    name: str | None = None,
    store: TraceStore | None = None,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    def decorator(
        fn: Callable[..., Awaitable[Any]],
    ) -> Callable[..., Awaitable[Any]]:
        tool_name = name or fn.__name__
        sig = inspect.signature(fn)

        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            trace_store = _resolve_store(store)
            trace_id = uuid.uuid4().hex
            arg_payload, raw_ctx = _process_arguments(sig, args, kwargs)
            ctx_str = _coerce_ctx(raw_ctx, tool_name=tool_name)

            recorder = trace_store.recorder(
                trace_id=trace_id,
                caller=f"mcp.{tool_name}",
                ctx=ctx_str,
            )

            invoke_payload: TracePayload = {"risk": risk}
            invoke_payload.update(arg_payload)
            recorder.append_stage("invoke", payload=invoke_payload)

            policy_decision = None
            if risk != "read":
                policy = _resolve_policy()
                policy_decision = policy.decide_tool_action(
                    risk=risk, ctx=ctx_str
                )
                recorder.append_policy_decision(policy_decision)
                if not policy_decision.allowed:
                    from axon.policy.core import PolicyDenied

                    exc = PolicyDenied(policy_decision)
                    recorder.append_stage(
                        "error",
                        payload={
                            "ok": False,
                            "latency_ms": 0,
                            "error_type": "PolicyDenied",
                            "error_msg": str(exc)[:200],
                            "reason_code": policy_decision.reason_code.value,
                        },
                    )
                    raise exc

            token = _CURRENT_RECORDER.set(recorder)
            start = time.perf_counter()
            try:
                result = await fn(*args, **kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                latency_ms = int((time.perf_counter() - start) * 1000)
                recorder.append_stage(
                    "error",
                    payload={
                        "ok": False,
                        "latency_ms": latency_ms,
                        "error_type": type(exc).__name__,
                        "error_msg": str(exc)[:200],
                    },
                )
                raise
            else:
                latency_ms = int((time.perf_counter() - start) * 1000)
                recorder.append_stage(
                    "output",
                    payload={
                        "ok": True,
                        "latency_ms": latency_ms,
                        "output_tokens": _estimate_tokens(result),
                    },
                )
                return result
            finally:
                _CURRENT_RECORDER.reset(token)

        return wrapper

    return decorator
