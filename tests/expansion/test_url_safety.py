from __future__ import annotations

import socket
from urllib.request import Request

import pytest

from axon.expansion import url_safety
from axon.expansion.url_safety import (
    GuardedRedirectHandler,
    build_guarded_opener,
    check_url_safety,
)


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "ftp://example.com/", "gopher://example.com/", "data:text/plain,hi"],
)
def test_check_url_safety_rejects_disallowed_schemes(url: str) -> None:
    with pytest.raises(ValueError):
        check_url_safety(url)


def test_check_url_safety_accepts_public_ip_literal() -> None:
    check_url_safety("http://93.184.216.34/")


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://169.254.169.254/",
        "http://10.0.0.1/",
        "http://192.168.1.1/",
        "http://[fc00::1]/",
        "http://0.0.0.0/",
        "http://100.100.100.200/",  # CGNAT / Shared Address Space, RFC 6598
        "http://224.0.0.1/",  # multicast IPv4 - is_global=True in Python 3.11
        "http://[ff02::1]/",  # multicast IPv6 - is_global=True in Python 3.11
        "http://[4000::1]/",  # reserved IPv6 - is_reserved AND is_global both True
    ],
)
def test_check_url_safety_rejects_non_public_addresses(url: str) -> None:
    with pytest.raises(ValueError):
        check_url_safety(url)


def test_guarded_redirect_handler_allows_safe_redirect() -> None:
    handler = GuardedRedirectHandler()
    req = Request("http://example.com/start")
    result = handler.redirect_request(req, None, 301, "Moved", {}, "http://example.com/next")
    assert result is not None
    assert result.full_url == "http://example.com/next"


def test_guarded_redirect_handler_blocks_unsafe_redirect() -> None:
    handler = GuardedRedirectHandler()
    req = Request("http://example.com/start")
    with pytest.raises(ValueError):
        handler.redirect_request(req, None, 301, "Moved", {}, "http://127.0.0.1/")


def test_build_guarded_opener_installs_guarded_redirect_handler() -> None:
    opener = build_guarded_opener()
    assert any(isinstance(h, GuardedRedirectHandler) for h in opener.handlers)


def test_check_url_safety_rejects_when_non_first_resolved_address_is_unsafe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # a hostname can resolve to multiple A records; check_url_safety must
    # reject if ANY of them is non-public, not just the first.
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 0)),
        ]

    monkeypatch.setattr(url_safety.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(ValueError):
        check_url_safety("http://multi-a-record.example.com/")
