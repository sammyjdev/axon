"""AXON OpenAI-compatible HTTP server.

Exposes ``POST /v1/chat/completions`` so external evaluators (e.g. gnomon-eval)
can measure recall quality over a standard chat-completions interface.

This module is intentionally additive — the MCP stdio path is unchanged.
The endpoint reuses the same retrieval pipeline (``_retrieve_context`` from
``axon.mcp.server``) and the same router/LLM call (``complete`` from
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
        "usage": {"total_tokens": <int>}
    }

The ``contexts`` list (top-level) and ``usage.total_tokens`` are *required* by
gnomon-eval and will always be present.
"""

from __future__ import annotations

import uuid
from typing import Any

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import JSONResponse
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
    from axon.router.engine import TaskRequest, complete  # noqa: PLC0415

    query = _last_user_message(request.messages)
    if not query:
        raise HTTPException(status_code=422, detail="No user message found in messages list.")

    # --- retrieval -------------------------------------------------------
    try:
        _raw_context, pack, _hits = await _retrieve_context(
            query=query,
            ctx=None,
            language=None,
            max_depth=2,
            max_nodes=25,
            max_tokens=4000,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Retrieval error: {exc}") from exc

    # Surface individual segment strings (not the combined formatted text).
    context_segments: list[str] = list(pack.segments)

    # Build a context-enriched user prompt for the LLM.
    context_block = "\n\n".join(context_segments) if context_segments else "(no context retrieved)"
    augmented_query = (
        f"Context retrieved from AXON:\n{context_block}\n\nQuestion: {query}"
    )

    # --- LLM completion --------------------------------------------------
    task = TaskRequest(content=augmented_query)
    try:
        answer = await complete(task, messages=[])
    except Exception as exc:
        # Surface retrieval context even when the LLM call fails so the
        # evaluator can still score recall from ``contexts``.
        answer = f"[LLM unavailable: {exc}]\n\nContext:\n{context_block}"

    # --- usage accounting ------------------------------------------------
    total_tokens = _estimate_tokens(augmented_query) + _estimate_tokens(answer)

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
        "usage": {"total_tokens": total_tokens},
    }
    return JSONResponse(content=body)


@app.get("/health")
async def health() -> JSONResponse:
    """Liveness probe — returns ``{"status": "ok"}``."""
    return JSONResponse(content={"status": "ok"})
