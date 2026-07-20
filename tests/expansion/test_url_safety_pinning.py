from __future__ import annotations

import io
import socket
from urllib.request import Request

import pytest

from axon.expansion import url_safety
from axon.expansion.url_safety import (
    PinnedHTTPHandler,
    PinnedHTTPSHandler,
    _PinnedHTTPConnection,
    _PinnedHTTPSConnection,
    build_guarded_opener,
    check_url_safety,
)


def _fake_getaddrinfo_single(ip: str):
    def fake_getaddrinfo(host, port, *args, **kwargs):  # noqa: ARG001 - stub matches real signature
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    return fake_getaddrinfo


def test_check_url_safety_returns_vetted_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        url_safety.socket, "getaddrinfo", _fake_getaddrinfo_single("93.184.216.34")
    )
    result = check_url_safety("http://example.com/")
    assert result == "93.184.216.34"


def test_pinned_connection_connects_to_pinned_ip() -> None:
    conn = _PinnedHTTPConnection("original-host.example.com", pinned_ip="93.184.216.34")
    captured: list = []

    def fake_create_connection(address, timeout, source_address):  # noqa: ARG001
        captured.append(address)
        return object()

    conn._create_connection = fake_create_connection
    conn.connect()

    assert len(captured) == 1
    assert captured[0][0] == "93.184.216.34"
    assert conn.host == "original-host.example.com"


def test_pinned_https_uses_hostname_for_sni() -> None:
    conn = _PinnedHTTPSConnection("original-host.example.com", pinned_ip="93.184.216.34")
    fake_socket = object()
    captured_kwargs: dict = {}

    def fake_create_connection(address, timeout, source_address):  # noqa: ARG001
        return fake_socket

    def fake_wrap_socket(sock, server_hostname=None, **kwargs):  # noqa: ARG001
        captured_kwargs["server_hostname"] = server_hostname
        return sock

    conn._create_connection = fake_create_connection
    conn._context.wrap_socket = fake_wrap_socket
    conn.connect()

    assert captured_kwargs["server_hostname"] == "original-host.example.com"


def test_rebinding_second_resolution_not_consulted(monkeypatch: pytest.MonkeyPatch) -> None:
    call_count = 0

    def fake_getaddrinfo(host, port, *args, **kwargs):  # noqa: ARG001
        nonlocal call_count
        call_count += 1
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(url_safety.socket, "getaddrinfo", fake_getaddrinfo)

    vetted_ip = check_url_safety("http://rebinding-target.example.com/")
    assert call_count == 1

    captured: list = []

    def fake_create_connection(address, timeout, source_address):  # noqa: ARG001
        captured.append(address)
        return object()

    conn = _PinnedHTTPConnection("rebinding-target.example.com", pinned_ip=vetted_ip)
    conn._create_connection = fake_create_connection
    conn.connect()

    # No second call to getaddrinfo happened between validation and connect -
    # the TCP connect targets exactly the IP that check_url_safety vetted, so
    # a second (possibly different, private) resolution is never consulted.
    assert call_count == 1
    assert captured[0][0] == vetted_ip == "93.184.216.34"


def test_opener_installs_pinning_handlers() -> None:
    opener = build_guarded_opener()
    assert any(isinstance(h, PinnedHTTPHandler) for h in opener.handlers)
    assert any(isinstance(h, PinnedHTTPSHandler) for h in opener.handlers)


class _FakeSocket:
    """Just enough of the socket API for http.client to send a request and
    parse a canned response, without touching the network."""

    def makefile(self, mode, *args, **kwargs):  # noqa: ARG002 - stub matches real signature
        return io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")

    def sendall(self, data) -> None:  # noqa: ARG002 - no-op stub
        pass

    def setsockopt(self, *args, **kwargs) -> None:  # noqa: ARG002 - stdlib connect() calls this
        pass

    def close(self) -> None:
        pass


def test_http_open_connects_to_pinned_ip_not_rebound_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end proof for the DNS-rebinding TOCTOU gap: drive the real
    PinnedHTTPHandler.http_open(req), not just _PinnedHTTPConnection.connect()
    in isolation, so a regression that re-plumbs http_open to drop the pinned
    IP (e.g. reverting to do_open(_PinnedHTTPConnection, req) without
    pinned_ip) is caught.

    A hostile resolver returns a public IP on the first call (the one
    check_url_safety vets) and a private IP on any later call (the one a
    rebinding attack would serve at connect time). http.client.HTTPConnection
    grabs socket.create_connection as an *instance* attribute in __init__
    (its own comment: "stored as an instance variable to allow unit tests to
    replace it") - do_open() constructs that instance internally, so
    class-level patching of _create_connection is ineffective; patching
    socket.create_connection itself is what __init__ picks up.
    """
    call_count = 0

    def hostile_getaddrinfo(host, port, *args, **kwargs):  # noqa: ARG001
        nonlocal call_count
        call_count += 1
        ip = "93.184.216.34" if call_count == 1 else "10.0.0.5"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    monkeypatch.setattr(url_safety.socket, "getaddrinfo", hostile_getaddrinfo)

    captured: list = []

    def fake_create_connection(address, timeout=None, source_address=None):  # noqa: ARG001
        captured.append(address)
        return _FakeSocket()

    monkeypatch.setattr(socket, "create_connection", fake_create_connection)

    req = Request("http://rebinding-target.example.com/")
    req.timeout = socket._GLOBAL_DEFAULT_TIMEOUT
    response = PinnedHTTPHandler().http_open(req)
    response.close()

    # Only the validating resolution happened - the connect used the pin,
    # never asking the (hostile) resolver a second time.
    assert call_count == 1
    assert captured == [("93.184.216.34", 80)]


def test_http_open_skips_pinning_when_proxied(monkeypatch: pytest.MonkeyPatch) -> None:
    """When a ProxyHandler has already rewritten req.host to the proxy's
    address (simulated here via Request.set_proxy, the same stdlib method
    ProxyHandler.proxy_open uses), PinnedHTTPHandler must fall back to the
    standard non-pinned connection instead of pinning the target's IP onto
    the proxy's port - see Finding 3: pinning through a proxy connects to
    (target_ip, proxy_port), which silently breaks or bypasses the proxy."""

    def fail_if_called(url):  # noqa: ARG001
        raise AssertionError("check_url_safety must not run for a proxied request")

    monkeypatch.setattr(url_safety, "check_url_safety", fail_if_called)

    captured: list = []

    def fake_create_connection(address, timeout=None, source_address=None):  # noqa: ARG001
        captured.append(address)
        return _FakeSocket()

    monkeypatch.setattr(socket, "create_connection", fake_create_connection)

    req = Request("http://target.example.com/path")
    req.timeout = socket._GLOBAL_DEFAULT_TIMEOUT
    # Simulate ProxyHandler.proxy_open: rewrites req.host to the proxy,
    # leaving req.full_url (the original target) untouched.
    req.set_proxy("proxy.example.com:9999", "http")

    response = PinnedHTTPHandler().http_open(req)
    response.close()

    # Connected straight to the proxy's own host:port (correct proxy
    # behavior) - not to the target's pinned IP with the proxy's port.
    assert captured == [("proxy.example.com", 9999)]
