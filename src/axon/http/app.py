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
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

try:
    from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request
    from fastapi.responses import HTMLResponse, JSONResponse
    from pydantic import BaseModel
except ModuleNotFoundError as _exc:  # pragma: no cover
    raise ModuleNotFoundError(
        "The 'http' extra is required for axon serve-http. "
        "Install it with: pip install axon-context-mcp[http]"
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


SESSION_COOKIE = "axon_dashboard"


def _presented_token(authorization: str, cookie: str) -> str:
    """The credential from either carrier: Bearer header, or session cookie.

    The browser cannot attach an Authorization header to the ``fetch`` calls
    the dashboard page makes on its own, so a header-only guard leaves the
    dashboard reachable exactly in the configuration the bind guard refuses
    (no token, loopback only). The cookie is set by ``GET /dashboard``, which
    is itself behind this dependency - so it is issued only to a caller that
    already presented the Bearer token. It is HttpOnly, so the page's own
    JavaScript never reads it, which keeps a future XSS in the dashboard from
    turning into credential theft.
    """
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return cookie


#: Liveness only. Carries no data, and the project's own readiness check
#: (`.github/workflows/ci.yml`) curls it without credentials. Listed here
#: rather than via a per-route `dependencies=[]`, which ADDS to the app-level
#: dependency instead of replacing it - the exemption has to be explicit.
_UNAUTHENTICATED_PATHS = frozenset({"/health"})


async def _require_bearer_token(
    request: Request,
    authorization: str = Header(default=""),
    axon_dashboard: str = Cookie(default=""),
) -> None:
    if request.url.path in _UNAUTHENTICATED_PATHS:
        return
    expected = os.environ.get("AXON_HTTP_TOKEN", "")
    if not expected:
        return
    presented = _presented_token(authorization, axon_dashboard)
    if presented and secrets.compare_digest(presented.encode(), expected.encode()):
        return
    raise HTTPException(status_code=401, detail="Unauthorized")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="AXON OpenAI-compatible API",
    description="Exposes AXON retrieval as an OpenAI chat-completions endpoint.",
    version="0.1.0",
    dependencies=[Depends(_require_bearer_token)],
)


@app.middleware("http")
async def _no_store(request, call_next):
    """Keep every response out of shared caches (#74).

    Applied globally rather than per-route: the endpoints return session
    activity, gain telemetry and promotion candidates, and a proxy cache that
    stores any of it can serve one user's data to another. Also covers 404s,
    which is what the ZAP baseline actually hit.
    """
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response.headers["Pragma"] = "no-cache"
    return response


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
    """Liveness probe — returns ``{"status": "ok"}``.

    Deliberately unauthenticated: it carries no data, and the project's own
    readiness check (`.github/workflows/ci.yml`) curls it without credentials.
    """
    return JSONResponse(content={"status": "ok"})


@app.get("/api/promotion-candidates")
async def api_promotion_candidates() -> JSONResponse:
    from axon.promotion import (  # noqa: PLC0415
        PromotionSourceError,
        load_promotion_candidates,
    )

    try:
        response = load_promotion_candidates()
    except PromotionSourceError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "detail": exc.detail},
        )
    return JSONResponse(content=response.model_dump(mode="json"))


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

    response = HTMLResponse(content=DASHBOARD_HTML)
    token = os.environ.get("AXON_HTTP_TOKEN", "")
    if token:
        # Reaching here means the Bearer token was already presented. Hand the
        # browser a cookie so the page's own fetches carry the credential the
        # browser cannot put in a header. HttpOnly keeps it out of reach of JS.
        #
        # Secure, because this cookie carries the token itself, not a derived
        # session id. The use case #93 was written for is `--host 0.0.0.0` so a
        # remote client can reach the service, and uvicorn speaks no TLS: without
        # this flag the full credential would cross the network in the clear on
        # every dashboard poll, which is the very thing the auth exists to stop.
        # Browsers treat http://localhost and http://127.0.0.1 as trustworthy
        # origins, so the loopback case keeps working; a plain-HTTP bind on a LAN
        # address loses the dashboard, and that is the correct trade - put TLS in
        # front of it. API clients are unaffected: they send the Bearer header.
        response.set_cookie(
            SESSION_COOKIE,
            token,
            httponly=True,
            secure=True,
            samesite="strict",
            path="/",
        )
    return response


@app.get("/dashboard/promotions", response_class=HTMLResponse)
async def promotions_dashboard() -> HTMLResponse:
    from axon.http.promotions_dashboard import (  # noqa: PLC0415
        PROMOTIONS_DASHBOARD_HTML,
    )

    return HTMLResponse(content=PROMOTIONS_DASHBOARD_HTML)
