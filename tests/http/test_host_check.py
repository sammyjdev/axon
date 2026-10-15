"""With no token, the HTTP surface answers loopback hosts only.

A page in the browser can point its own domain at 127.0.0.1 (DNS rebinding) and
then read a token-less loopback server as same-origin. The Host header is what
gives that away: it carries the attacker's domain, not a loopback name.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest

pytest.importorskip("fastapi", reason="install the `http` extra to run these")
pytest.importorskip("httpx", reason="install the `http` extra to run these")

from fastapi.testclient import TestClient

from axon.http.app import app


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.mark.parametrize("host", ["evil.example:8765", "127.0.0.1.evil.example", "10.0.0.5:8765"])
def test_without_a_token_a_request_for_another_host_is_refused(
    client: TestClient, host: str
) -> None:
    response = client.get("/api/gain", headers={"host": host})

    assert response.status_code == 400


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1:8765",
        "localhost:8765",
        "[::1]:8765",
        "LOCALHOST",
        "127.0.0.53:8765",
        "[0:0:0:0:0:0:0:1]:8765",
    ],
)
def test_without_a_token_loopback_hosts_are_served(client: TestClient, host: str) -> None:
    response = client.get("/api/gain", headers={"host": host})

    assert response.status_code == 200


def test_with_a_token_a_remote_host_is_served(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    monkeypatch.setenv("AXON_HTTP_TOKEN", "secret-bearer-token")  # noqa: S105

    response = client.get(
        "/api/gain",
        headers={"host": "axon.lan:8765", "authorization": "Bearer secret-bearer-token"},
    )

    assert response.status_code == 200


def test_an_explicit_unauthenticated_opt_in_serves_a_remote_host(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    monkeypatch.setenv("AXON_HTTP_ALLOW_UNAUTHENTICATED", "1")

    response = client.get("/api/gain", headers={"host": "192.168.1.5:8765"})

    assert response.status_code == 200


def test_the_liveness_probe_answers_any_host(client: TestClient) -> None:
    response = client.get("/health", headers={"host": "evil.example"})

    assert response.status_code == 200
