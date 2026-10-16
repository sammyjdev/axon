"""AXON HTTP server: the dashboards and the activity API.

Start with::

    axon serve-http --port 8765

The OpenAI-compatible ``/v1/chat/completions`` endpoint that served gnomon-eval
went with code search and the router (dec-138).
"""

from __future__ import annotations

import ipaddress
import logging
import os
import secrets
from typing import Any

from axon.config.runtime import load_runtime_config

_RUNTIME = load_runtime_config()

logger = logging.getLogger(__name__)

try:
    from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Query, Request
    from fastapi.responses import HTMLResponse, JSONResponse
except ModuleNotFoundError as _exc:  # pragma: no cover
    raise ModuleNotFoundError(
        "The 'http' extra is required for axon serve-http. "
        "Install it with: pip install axon-context-mcp[http]"
    ) from _exc

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

#: With no token the server trusts whoever reaches it, which is only safe while
#: "whoever" is this machine. A page can point its own domain at 127.0.0.1 (DNS
#: rebinding) and read a token-less server as same-origin; the Host header still
#: names that domain, so only loopback hosts are served.


def _host_name(host_header: str) -> str:
    """The host of a Host header, without port or IPv6 brackets, lowercased."""
    host = host_header.strip().lower()
    if host.startswith("["):
        return host[1 : host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def _is_loopback_host(host: str) -> bool:
    """`localhost`, or any loopback IP: the same set serve-http lets you bind."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


async def _require_bearer_token(
    request: Request,
    authorization: str = Header(default=""),
    axon_dashboard: str = Cookie(default=""),
) -> None:
    if request.url.path in _UNAUTHENTICATED_PATHS:
        return
    expected = os.environ.get("AXON_HTTP_TOKEN", "")
    if not expected:
        unauthenticated_opt_in = os.environ.get(
            "AXON_HTTP_ALLOW_UNAUTHENTICATED", ""
        ).strip().lower() in {"1", "true", "yes", "on"}
        host = _host_name(request.headers.get("host", ""))
        if not unauthenticated_opt_in and not _is_loopback_host(host):
            raise HTTPException(status_code=400, detail="Invalid host header")
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
    description="AXON dashboards and activity API.",
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


# ---------------------------------------------------------------------------
# Activity History API (Task 9)
# ---------------------------------------------------------------------------

_ACTIVITY_API_MAX_LIMIT = 100

async def _get_activity_service():
    from axon.activity.repository import PostgresActivityRepository  # noqa: PLC0415
    from axon.activity.service import ActivityService  # noqa: PLC0415

    repo = PostgresActivityRepository(_RUNTIME.pg_url)
    await repo.ensure_schema()
    try:
        yield ActivityService(repo)
    finally:
        await repo.close()


@app.get("/api/activity/sessions")
async def api_activity_sessions(
    limit: int = Query(50, gt=0, le=_ACTIVITY_API_MAX_LIMIT),
    cursor: str | None = None,
    project: str | None = None,
    harness: str | None = None,
    service: Any = Depends(_get_activity_service),
) -> JSONResponse:
    if cursor is not None:
        try:
            int(cursor)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid cursor format") from exc

    sessions, next_cursor = await service.repo.list_sessions(
        project=project, harness=harness, limit=limit, cursor=cursor
    )
    
    return JSONResponse(
        content={
            "sessions": [s.model_dump(mode="json") for s in sessions],
            "next_cursor": next_cursor,
        }
    )


@app.get("/api/activity/sessions/{session_id}/timeline")
async def api_activity_session_timeline(
    session_id: str,
    service: Any = Depends(_get_activity_service),
) -> JSONResponse:
    events = await service.get_session_timeline(session_id)
    return JSONResponse(
        content=[e.model_dump(mode="json") for e in events]
    )


@app.get("/api/activity/search")
async def api_activity_search(
    q: str,
    limit: int = Query(50, gt=0, le=_ACTIVITY_API_MAX_LIMIT),
    cursor: str | None = None,
    project: str | None = None,
    harness: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    outcome: str | None = None,
    service: Any = Depends(_get_activity_service),
) -> JSONResponse:
    from axon.activity.models import ActivityFilters  # noqa: PLC0415

    if cursor is not None:
        try:
            int(cursor)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid cursor format") from exc

    filters = ActivityFilters(
        project=project,
        harness=harness,  # type: ignore
        date_from=date_from,  # type: ignore
        date_to=date_to,  # type: ignore
        outcome=outcome,
    )
    
    page = await service.search_activity(
        q, filters=filters, limit=limit, cursor=cursor
    )
    
    return JSONResponse(
        content=page.model_dump(mode="json")
    )


@app.get("/api/activity/health")
async def api_activity_health(
    service: Any = Depends(_get_activity_service),
) -> JSONResponse:
    import dataclasses
    
    report = await service.health()
    return JSONResponse(content=dataclasses.asdict(report))
