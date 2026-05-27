from __future__ import annotations

import asyncio
import hashlib
import inspect
import time
import uuid
from contextvars import ContextVar
from functools import wraps
from typing import Any, Awaitable, Callable, Literal

from axon.observability.trace_store import TracePayload, TraceRecorder, TraceStore

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
    for name, value in bound.arguments.items():
        if len(payload) >= _MAX_PAYLOAD_KEYS:
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
    return payload


def _estimate_tokens(result: Any) -> int:
    if result is None:
        return 0
    if isinstance(result, str):
        return len(result) // 4
    try:
        return len(str(result)) // 4
    except Exception:
        return 0


def _resolve_store(store: TraceStore | None) -> TraceStore:
    if store is not None:
        return store
    # Imported lazily to avoid a circular import at module load time.
    from axon.mcp import server as _server

    return _server._TRACE_STORE


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
            try:
                bound = sig.bind_partial(*args, **kwargs)
                bound.apply_defaults()
                arg_payload = _summarize_args(bound)
            except TypeError:
                arg_payload = {}
            ctx_value = bound.arguments.get("ctx") if "ctx" in bound.arguments else None
            ctx_str = ctx_value if isinstance(ctx_value, str) else None

            recorder = trace_store.recorder(
                trace_id=trace_id,
                caller=f"mcp.{tool_name}",
                ctx=ctx_str,
            )

            invoke_payload: TracePayload = {"risk": risk}
            invoke_payload.update(arg_payload)
            recorder.append_stage("invoke", payload=invoke_payload)

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
