from __future__ import annotations

import logging
from collections.abc import Generator
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("fastapi", reason="install the `http` extra to run these")
pytest.importorskip("httpx", reason="install the `http` extra to run these")

from fastapi.testclient import TestClient

from axon.http.app import app


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.mark.parametrize(
    "path",
    [
        "/health",
        "/api/gain",
        "/api/activity",
        "/api/promotion-candidates",
        "/dashboard",
        "/dashboard/promotions",
    ],
)
def test_every_route_requires_the_token_when_configured(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, path: str
) -> None:
    monkeypatch.setenv("AXON_HTTP_TOKEN", "secret-bearer-token")  # noqa: S105
    response = client.get(path)
    assert response.status_code == 401


def test_an_unauthenticated_chat_completion_never_reaches_retrieval_or_the_llm(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    monkeypatch.setenv("AXON_HTTP_TOKEN", "secret-bearer-token")  # noqa: S105
    mock_retrieve = AsyncMock()
    mock_complete = AsyncMock()
    monkeypatch.setattr("axon.mcp.server._retrieve_context", mock_retrieve)
    monkeypatch.setattr("axon.router.engine.complete_with_usage", mock_complete)

    body = {"messages": [{"role": "user", "content": "hello"}]}
    response = client.post("/v1/chat/completions", json=body)

    assert response.status_code == 401
    mock_retrieve.assert_not_called()
    mock_complete.assert_not_called()


def test_the_correct_bearer_token_is_accepted(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    monkeypatch.setenv("AXON_HTTP_TOKEN", "secret-bearer-token")  # noqa: S105
    headers = {"Authorization": "Bearer secret-bearer-token"}  # noqa: S105
    response = client.get("/health", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    "auth_header",
    [
        None,
        "Bearer wrong-token",  # noqa: S105
        "Bearer ",  # noqa: S105
        "Basic secret-bearer-token",  # noqa: S105
        "secret-bearer-token",  # noqa: S105
    ],
)
def test_bad_credentials_are_rejected(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, auth_header: str | None
) -> None:
    monkeypatch.setenv("AXON_HTTP_TOKEN", "secret-bearer-token")  # noqa: S105
    headers = {"Authorization": auth_header} if auth_header is not None else {}
    response = client.get("/health", headers=headers)

    assert response.status_code == 401


def test_bearer_token_case_insensitive_scheme(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    monkeypatch.setenv("AXON_HTTP_TOKEN", "secret-bearer-token")  # noqa: S105
    headers = {"Authorization": "bearer secret-bearer-token"}  # noqa: S105
    response = client.get("/health", headers=headers)

    assert response.status_code == 200


def test_a_non_ascii_configured_token_does_not_crash_the_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AXON_HTTP_TOKEN", "töken-123")  # noqa: S105
    client = TestClient(app, raise_server_exceptions=False)
    headers = {"Authorization": "Bearer wrong"}  # noqa: S105
    response = client.get("/health", headers=headers)

    assert response.status_code == 401, (
        "non-ASCII token must not raise TypeError from compare_digest"
    )


def test_the_token_never_appears_in_a_response(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    token = "super-secret-token-xyz"  # noqa: S105
    monkeypatch.setenv("AXON_HTTP_TOKEN", token)

    res1 = client.get("/health", headers={"Authorization": f"Bearer {token}"})
    assert token not in res1.text
    assert token not in str(res1.headers)

    res2 = client.get("/health", headers={"Authorization": "Bearer wrong"})
    assert token not in res2.text
    assert token not in str(res2.headers)


def test_the_token_never_appears_in_the_logs(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    token = "super-secret-token-xyz"  # noqa: S105
    monkeypatch.setenv("AXON_HTTP_TOKEN", token)

    caplog.set_level(logging.DEBUG)
    client.get("/health", headers={"Authorization": f"Bearer {token}"})
    client.get("/health", headers={"Authorization": "Bearer wrong"})

    assert token not in caplog.text


@pytest.mark.parametrize("env_val", [None, ""])
def test_auth_is_disabled_when_the_token_is_unset_or_empty(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, env_val: str | None
) -> None:
    if env_val is None:
        monkeypatch.delenv("AXON_HTTP_TOKEN", raising=False)
    else:
        monkeypatch.setenv("AXON_HTTP_TOKEN", env_val)

    response = client.get("/health")
    assert response.status_code == 200
