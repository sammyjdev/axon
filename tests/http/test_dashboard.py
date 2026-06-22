"""Tests for the AXON read-only dashboard routes (dec-119 step 4).

Routes covered:
  GET /api/gain       → 200 JSON with ``saved_tokens`` key
  GET /api/activity   → 200 JSON list (empty store → [])
  GET /dashboard      → 200 HTML containing "AXON"

All canonical-store calls are exercised against the empty-store defaults (no
real JSONL files needed); zeros/empty are valid responses.

Guards follow the pattern in ``test_chat_completions.py``:
  pytest.importorskip("fastapi") / pytest.importorskip("httpx")
so the suite *skips* (not errors) in environments where FastAPI is absent and
*passes* where it is installed.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed; skipping dashboard tests")
pytest.importorskip("httpx", reason="httpx not installed; skipping dashboard tests")

from fastapi.testclient import TestClient  # noqa: E402 (after importorskip guards)

from axon.http.app import app  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """TestClient backed by the shared AXON FastAPI app."""
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ---------------------------------------------------------------------------
# GET /api/gain
# ---------------------------------------------------------------------------


def test_gain_returns_200(client: TestClient) -> None:
    resp = client.get("/api/gain")
    assert resp.status_code == 200


def test_gain_has_saved_tokens_key(client: TestClient) -> None:
    body = client.get("/api/gain").json()
    assert "saved_tokens" in body, "GainSummary must expose 'saved_tokens'"


def test_gain_body_is_object(client: TestClient) -> None:
    body = client.get("/api/gain").json()
    assert isinstance(body, dict)


def test_gain_has_all_expected_keys(client: TestClient) -> None:
    body = client.get("/api/gain").json()
    expected = {
        "windows",
        "compressed",
        "saved_tokens",
        "before_tokens",
        "after_tokens",
        "p50_pct",
        "mean_pct",
        "p95_pct",
        "max_pct",
        "by_engine",
        "daily_saved",
    }
    missing = expected - body.keys()
    assert not missing, f"GainSummary response is missing keys: {missing}"


def test_gain_empty_store_returns_zeros(client: TestClient) -> None:
    """With no telemetry on disk, saved_tokens must be 0 (not an error)."""
    body = client.get("/api/gain").json()
    assert body["saved_tokens"] == 0
    assert body["windows"] == 0


# ---------------------------------------------------------------------------
# GET /api/activity
# ---------------------------------------------------------------------------


def test_activity_returns_200(client: TestClient) -> None:
    resp = client.get("/api/activity")
    assert resp.status_code == 200


def test_activity_body_is_list(client: TestClient) -> None:
    body = client.get("/api/activity").json()
    assert isinstance(body, list)


def test_activity_empty_store_returns_empty_list(client: TestClient) -> None:
    """With no trace records on disk the response must be []."""
    body = client.get("/api/activity").json()
    assert body == []


def test_activity_default_limit_param(client: TestClient) -> None:
    """Endpoint must accept ``limit`` query param without error."""
    resp = client.get("/api/activity?limit=10")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_activity_limit_capped(client: TestClient) -> None:
    """A limit larger than 500 must not cause an error."""
    resp = client.get("/api/activity?limit=9999")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# GET /dashboard
# ---------------------------------------------------------------------------


def test_dashboard_returns_200(client: TestClient) -> None:
    resp = client.get("/dashboard")
    assert resp.status_code == 200


def test_dashboard_content_type_is_html(client: TestClient) -> None:
    resp = client.get("/dashboard")
    assert resp.headers["content-type"].startswith("text/html")


def test_dashboard_body_contains_axon(client: TestClient) -> None:
    resp = client.get("/dashboard")
    assert "AXON" in resp.text, "Dashboard page must mention 'AXON'"


def test_dashboard_body_has_api_gain_fetch(client: TestClient) -> None:
    """JS in the page must reference /api/gain so polling is wired up."""
    resp = client.get("/dashboard")
    assert "/api/gain" in resp.text


def test_dashboard_body_has_api_activity_fetch(client: TestClient) -> None:
    """JS in the page must reference /api/activity so the feed is wired up."""
    resp = client.get("/dashboard")
    assert "/api/activity" in resp.text


def test_dashboard_is_self_contained(client: TestClient) -> None:
    """Page must not reference external CDN URLs."""
    resp = client.get("/dashboard")
    body = resp.text
    forbidden = ["cdn.jsdelivr.net", "unpkg.com", "cdnjs.cloudflare.com"]
    for url in forbidden:
        assert url not in body, f"Dashboard must not load from external CDN: {url}"
