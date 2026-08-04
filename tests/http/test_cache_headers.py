"""Responses must not be storable by shared caches (#74).

The ZAP baseline flagged "Storable and Cacheable Content" on this server. The
endpoints below return session activity, gain telemetry and promotion
candidates — user-specific data that a proxy cache must never hand to someone
else.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from axon.http.app import app


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.mark.parametrize(
    "path",
    ["/health", "/api/gain", "/api/activity", "/api/promotion-candidates", "/dashboard"],
)
def test_responses_are_not_cacheable(client, path: str) -> None:
    cache_control = client.get(path).headers.get("cache-control", "")
    assert "no-store" in cache_control, (
        f"{path} returned Cache-Control={cache_control!r}; a shared cache may store it"
    )


def test_unmatched_route_is_not_cacheable(client) -> None:
    """The scanner hit `/`, which has no route — the 404 must carry the header too."""
    assert "no-store" in client.get("/").headers.get("cache-control", "")
