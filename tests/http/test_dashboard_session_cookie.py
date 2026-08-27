"""The dashboard must work with auth on, without the token reaching the page.

#93 put every route behind a Bearer dependency. #94 hardened the dashboard
against XSS. Together they left the dashboard reachable only in the one
configuration #93 declares unsafe: no token, loopback only. The page fetches
``/api/gain`` and ``/api/activity`` every 3s and a browser cannot attach an
Authorization header to those, so with a token set the page 401s on itself.

The cookie closes that without handing the credential to JavaScript.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from axon.http.app import SESSION_COOKIE, app

TOKEN = "test-token-value"  # noqa: S105


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AXON_HTTP_TOKEN", TOKEN)
    return TestClient(app)


def test_dashboard_still_requires_the_bearer_token(client: TestClient) -> None:
    assert client.get("/dashboard").status_code == 401


def test_dashboard_issues_an_httponly_cookie_to_an_authorised_caller(
    client: TestClient,
) -> None:
    response = client.get("/dashboard", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 200
    cookie = response.cookies.get(SESSION_COOKIE)
    assert cookie == TOKEN

    set_cookie = response.headers["set-cookie"].lower()
    assert "httponly" in set_cookie, "JS must not be able to read the credential"
    assert "samesite=strict" in set_cookie


def test_api_routes_accept_the_cookie_so_the_page_can_poll(client: TestClient) -> None:
    client.get("/dashboard", headers={"Authorization": f"Bearer {TOKEN}"})

    # The browser now replays only the cookie - no Authorization header, which
    # is exactly what the page's fetch() calls do.
    for route in ("/api/gain", "/api/activity"):
        assert client.get(route).status_code == 200, f"{route} 401s the dashboard"


def test_a_wrong_cookie_is_still_rejected(client: TestClient) -> None:
    client.cookies.set(SESSION_COOKIE, "not-the-token")

    assert client.get("/api/gain").status_code == 401


def test_no_cookie_is_issued_when_auth_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AXON_HTTP_TOKEN", raising=False)
    unauthenticated = TestClient(app)

    response = unauthenticated.get("/dashboard")

    assert response.status_code == 200
    assert SESSION_COOKIE not in response.cookies


def test_health_stays_reachable_without_credentials(client: TestClient) -> None:
    """The project's own readiness check curls /health with no credential."""
    assert client.get("/health").status_code == 200
