"""AXON OpenAI-compatible HTTP server.

Exposes ``POST /v1/chat/completions`` so external evaluators (e.g. gnomon-eval)
can measure recall quality over a standard chat-completions interface.

This module is intentionally additive — the MCP stdio path is unchanged.
The endpoint reuses the same retrieval pipeline (``_retrieve_context`` from
``axon.mcp.server``) and the same router/LLM call (``complete_with_usage`` from
``axon.router.engine``) to guarantee consistent behaviour across both transports.

Usage
-----
Start with::

    axon serve-http --port 8765

Then point gnomon's ``config/axon.toml`` at ``http://localhost:8765/v1``.

Response shape
--------------
The endpoint returns a JSON object that is a superset of the OpenAI
chat-completions response:

.. code-block:: json

    {
        "id": "axon-<uuid>",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "<answer>"},
                "finish_reason": "stop"
            }
        ],
        "contexts": ["<segment text>", ...],
        "usage": {
            "prompt_tokens": <int>,
            "completion_tokens": <int>,
            "total_tokens": <int>,
            "source": "provider" | "estimate"
        }
    }

The ``contexts`` list (top-level) and ``usage.total_tokens`` are *required* by
gnomon-eval and will always be present.

Request field ``include_context`` (bool, default ``true``) toggles retrieval:
when ``false``, no retrieval call is made, ``contexts`` is empty, and the LLM
receives the raw query — the recall-off baseline arm for A/B evals.
Request field ``forward_history`` (bool, default ``false``) forwards prior
messages to the router for multi-turn eval baseline arms.
Request field ``recall_max_tokens`` (int | null, default ``null``) overrides
the per-request retrieval budget.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse, JSONResponse
    from pydantic import BaseModel
except ModuleNotFoundError as _exc:  # pragma: no cover
    raise ModuleNotFoundError(
        "The 'http' extra is required for axon serve-http. "
        "Install it with: pip install axon-mcp[http]"
    ) from _exc

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class _Message(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "axon"
    messages: list[_Message]
    include_context: bool = True
    forward_history: bool = False
    recall_max_tokens: int | None = None


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="AXON OpenAI-compatible API",
    description="Exposes AXON retrieval as an OpenAI chat-completions endpoint.",
    version="0.1.0",
)


def _last_user_message(messages: list[_Message]) -> str:
    """Return the content of the last user-role message."""
    for msg in reversed(messages):
        if msg.role == "user":
            return msg.content
    return ""


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest) -> JSONResponse:
    """OpenAI-compatible chat completions backed by AXON retrieval.

    The last ``user`` message is used as the retrieval query.  Retrieved
    segments are surfaced verbatim in the top-level ``contexts`` list so that
    gnomon-eval can score recall quality without parsing the assistant answer.
    """
    # Import lazily so the module can be imported even before the stores are
    # initialised (important for unit tests that monkeypatch these callables).
    from axon.mcp.server import _retrieve_context  # noqa: PLC0415
    from axon.router.engine import TaskRequest, complete_with_usage  # noqa: PLC0415

    query = _last_user_message(request.messages)
    if not query:
        raise HTTPException(status_code=422, detail="No user message found in messages list.")

    # --- retrieval -------------------------------------------------------
    if request.include_context:
        retrieval_kwargs: dict[str, Any] = {}
        if request.forward_history and os.environ.get("AXON_DELTA_RECALL") == "1":
            retrieval_kwargs["dedup_against"] = [m.content for m in request.messages[:-1]]
        try:
            _raw_context, pack, hits = await _retrieve_context(
                query=query,
                ctx=None,
                language=None,
                max_depth=2,
                max_nodes=25,
                max_tokens=request.recall_max_tokens or 4000,
                **retrieval_kwargs,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Retrieval error: {exc}") from exc

        # Surface individual segment strings (not the combined formatted text).
        context_segments: list[str] = list(pack.segments)
        context_block = (
            "\n\n".join(context_segments) if context_segments else "(no context retrieved)"
        )
        all_context_dropped = bool(
            retrieval_kwargs.get("dedup_against") and not context_segments and hits
        )
        if all_context_dropped:
            augmented_query = query
        else:
            augmented_query = (
                f"Context retrieved from AXON:\n{context_block}\n\nQuestion: {query}"
            )
    else:
        # Recall disabled (A/B baseline): raw query, no retrieval cost.
        context_segments = []
        context_block = "(recall disabled)"
        augmented_query = query

    # --- LLM completion --------------------------------------------------
    # Conversation history for the baseline arm of multi-turn evals
    # (ADR-009 in gnomon-eval). Default [] preserves Wave 1 behavior.
    history: list[dict] = (
        [m.model_dump() for m in request.messages[:-1]] if request.forward_history else []
    )
    task = TaskRequest(content=augmented_query)
    try:
        answer, usage = await complete_with_usage(task, messages=history)
    except Exception as exc:
        # Surface retrieval context even when the LLM call fails so the
        # evaluator can still score recall from ``contexts``.
        answer = f"[LLM unavailable: {exc}]\n\nContext:\n{context_block}"
        usage = None

    # --- usage accounting -------------------------------------------------
    # Provider-reported numbers when available; a labeled estimate otherwise.
    # An eval run is only honest if every request reports source="provider".
    if usage is not None:
        usage_source = "provider"
        prompt_tokens = usage.prompt_tokens
        completion_tokens = usage.completion_tokens
        total_tokens = usage.total_tokens
        model_used = usage.model
    else:
        usage_source = "estimate"
        prompt_tokens = _estimate_tokens(augmented_query)
        completion_tokens = _estimate_tokens(answer)
        total_tokens = prompt_tokens + completion_tokens
        model_used = request.model

    # --- telemetry ---------------------------------------------------------
    from axon.observability.recall_telemetry import (  # noqa: PLC0415
        RecallRecord,
        RecallTelemetryStore,
    )

    record = RecallRecord(
        ts=datetime.now(UTC).isoformat(),
        caller="http",
        include_context=request.include_context,
        model=model_used,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        usage_source=usage_source,
    )
    try:
        RecallTelemetryStore().append(record)
    except OSError:
        logger.warning("recall telemetry append failed", exc_info=True)

    response_id = f"axon-{uuid.uuid4().hex[:12]}"
    body: dict[str, Any] = {
        "id": response_id,
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": answer},
                "finish_reason": "stop",
            }
        ],
        "contexts": context_segments,
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "source": usage_source,
        },
    }
    return JSONResponse(content=body)


@app.get("/health")
async def health() -> JSONResponse:
    """Liveness probe — returns ``{"status": "ok"}``."""
    return JSONResponse(content={"status": "ok"})


# ---------------------------------------------------------------------------
# Dashboard — read-only observability routes (dec-119)
# ---------------------------------------------------------------------------

_ACTIVITY_DEFAULT_LIMIT = 50
_ACTIVITY_MAX_LIMIT = 500


@app.get("/api/gain")
async def api_gain() -> JSONResponse:
    """Return aggregated compression-gain statistics from the canonical store.

    Delegates entirely to ``load_gain()`` (observability/gain.py) which applies
    the T-104 pollution filter before aggregating.  An empty or missing store
    returns all-zero / null-percentile summary — never an error.
    """
    from axon.observability.gain import load_gain  # noqa: PLC0415

    summary = load_gain()
    return JSONResponse(content=summary.model_dump())


@app.get("/api/activity")
async def api_activity(limit: int = _ACTIVITY_DEFAULT_LIMIT) -> JSONResponse:
    """Return the most-recent trace records from the canonical TraceStore.

    Query params
    ------------
    limit : int
        Maximum number of records to return (default 50, capped at 500).
        Records are ordered most-recent-first.  An empty store returns ``[]``.
    """
    from axon.observability.trace_store import TraceStore  # noqa: PLC0415

    cap = min(max(1, limit), _ACTIVITY_MAX_LIMIT)
    all_records = TraceStore().load_all()
    # Most-recent-first: take the tail of the append-only list then reverse.
    recent = list(reversed(all_records[-cap:])) if all_records else []
    return JSONResponse(content=[r.model_dump() for r in recent])


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    """Self-contained read-only web dashboard (dec-119 step 4).

    Renders a minimal HTML page that polls ``/api/gain`` and ``/api/activity``
    every 3 seconds via vanilla JS (no external CDN).
    """
    from axon.http.dashboard import DASHBOARD_HTML  # noqa: PLC0415

    return HTMLResponse(content=DASHBOARD_HTML)
