from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest

from axon.expansion import transport
from axon.expansion import url_safety as url_safety_module
from axon.expansion.models import SourceDefinition, SourceFormat
from axon.expansion.transport import UrllibSourceTransport
from axon.expansion.url_safety import GuardedRedirectHandler

try:
    from urllib.request import Request
except ImportError:  # pragma: no cover - stdlib always has this
    Request = None  # type: ignore[assignment]


def _make_source(endpoint: str) -> SourceDefinition:
    return SourceDefinition(
        source_id="src-1",
        name="Test Source",
        endpoint=endpoint,
        format=SourceFormat.RSS,
        headers=(("Authorization", "Bearer secret"),),
    )


class _FakeResponseHeaders:
    def get_content_charset(self) -> str | None:
        return "utf-8"

    def get(self, key: str, default: Any = None) -> Any:  # noqa: ARG002 - fixed stub value
        return "text/plain" if key == "Content-Type" else default


class _FakeResponse:
    def __init__(self, url: str) -> None:
        self._url = url
        self.status = 200
        self.headers = _FakeResponseHeaders()

    def read(self) -> bytes:
        return b"ok"

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        return None


@contextmanager
def _capture_request(captured: list) -> Any:
    def fake_open(request, timeout=None):  # noqa: ARG001 - matches _OPENER.open signature
        captured.append(request)
        return _FakeResponse(request.full_url)

    yield fake_open


@pytest.mark.asyncio
async def test_same_origin_fetch_keeps_source_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _make_source("https://api.example.com/feed")
    captured: list = []
    monkeypatch.setattr(transport, "check_url_safety", lambda url: None)
    with _capture_request(captured) as fake_open:
        monkeypatch.setattr(transport._OPENER, "open", fake_open)
        await UrllibSourceTransport().fetch("https://api.example.com/feed/article", source)

    assert len(captured) == 1
    request = captured[0]
    assert request.get_header("Authorization") == "Bearer secret"


@pytest.mark.asyncio
async def test_cross_origin_fetch_strips_source_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _make_source("https://api.example.com/feed")
    captured: list = []
    monkeypatch.setattr(transport, "check_url_safety", lambda url: None)
    with _capture_request(captured) as fake_open:
        monkeypatch.setattr(transport._OPENER, "open", fake_open)
        await UrllibSourceTransport().fetch("https://other-host.example.com/article", source)

    assert len(captured) == 1
    request = captured[0]
    assert request.get_header("Authorization") is None
    assert request.get_header("User-agent") == "AxonExpansion/1.0"


def test_redirect_cross_origin_strips_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(url_safety_module, "check_url_safety", lambda url: None)
    handler = GuardedRedirectHandler()
    req = Request(
        "http://a.example.com/start",
        headers={"Authorization": "secret-token", "User-Agent": "ua"},
    )
    result = handler.redirect_request(
        req, None, 301, "Moved", {}, "http://b.example.com/next"
    )
    assert result is not None
    assert result.get_header("Authorization") is None
    assert result.get_header("User-agent") == "ua"


def test_redirect_cross_origin_strips_arbitrary_source_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for an allowlist bypass: a source-configured header that is
    not one of the 3 well-known sensitive names (e.g. a custom API-key
    header) must still be stripped cross-origin - the redirect-hop policy
    keeps only a small safe allowlist (User-Agent/Accept/*) rather than
    denylisting a fixed set of names."""
    monkeypatch.setattr(url_safety_module, "check_url_safety", lambda url: None)
    handler = GuardedRedirectHandler()
    req = Request(
        "http://a.example.com/start",
        headers={"X-Api-Key": "secret-value", "User-Agent": "ua"},
    )
    result = handler.redirect_request(
        req, None, 301, "Moved", {}, "http://b.example.com/next"
    )
    assert result is not None
    assert result.get_header("X-Api-Key") is None
    assert result.get_header("User-agent") == "ua"


def test_redirect_same_origin_keeps_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(url_safety_module, "check_url_safety", lambda url: None)
    handler = GuardedRedirectHandler()
    req = Request(
        "http://a.example.com/start",
        headers={"Authorization": "secret-token", "User-Agent": "ua"},
    )
    result = handler.redirect_request(
        req, None, 301, "Moved", {}, "http://a.example.com/next"
    )
    assert result is not None
    assert result.get_header("Authorization") == "secret-token"
