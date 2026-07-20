from __future__ import annotations

import functools
import http.client
import ipaddress
import socket
from urllib.parse import urlsplit
from urllib.request import (
    HTTPHandler,
    HTTPRedirectHandler,
    HTTPSHandler,
    OpenerDirector,
    ProxyHandler,
    build_opener,
)

_ALLOWED_SCHEMES = frozenset({"http", "https"})

_DEFAULT_PORTS = {"http": 80, "https": 443}

# Headers kept on a cross-origin redirect hop - everything else (including
# any header a SourceDefinition configured, e.g. a custom API-key header) is
# stripped. This mirrors the initial-fetch policy in transport.py, which
# drops the ENTIRE source.headers block cross-origin (allowlist-by-origin,
# not a denylist) - a denylist of a few well-known sensitive names would
# still leak an arbitrary source-defined header to the new origin.
_SAFE_REDIRECT_HEADERS = frozenset({"user-agent", "accept", "accept-encoding", "accept-language"})


def _same_origin(url_a: str, url_b: str) -> bool:
    """Compare scheme+host+port, normalizing default ports (http:80, https:443)."""
    a = urlsplit(url_a)
    b = urlsplit(url_b)
    # `urlsplit(...).port` is None when no port is given, and an int
    # (including 0) when one is explicit - `or` would treat an explicit
    # port 0 as falsy and silently replace it with the default port.
    port_a = a.port if a.port is not None else _DEFAULT_PORTS.get(a.scheme)
    port_b = b.port if b.port is not None else _DEFAULT_PORTS.get(b.scheme)
    return (a.scheme, a.hostname, port_a) == (b.scheme, b.hostname, port_b)


# Explicit special-use denylist for ranges Python 3.11's is_global misses (all
# report is_global=True and are neither multicast nor reserved, so the check
# below lets them through). Enumerated from a full is_global/is_reserved/
# is_multicast scan (Codex cross-review of PR #95); do not treat is_global as
# sufficient on 3.11.
# - 192.0.0.0/24: IETF Protocol Assignments (RFC 6890), e.g. 192.0.0.8.
# - 192.88.99.0/24: 6to4 relay anycast (RFC 7526) - can pivot via a relay.
# - 2002::/16: 6to4 - embeds an arbitrary IPv4 (incl. private), e.g.
#   2002:0a00:0005:: is 10.0.0.5.
# - 3fff::/20: IPv6 documentation prefix (RFC 9637).
# The NAT64 well-known prefix 64:ff9b::/96 (also an IPv4-embedding pivot) is NOT
# listed here: it is inside ::/8 and so already blocked by the is_reserved check.
_SPECIAL_USE_DENYLIST = (
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.88.99.0/24"),
    ipaddress.ip_network("2002::/16"),
    ipaddress.ip_network("3fff::/20"),
)


def check_url_safety(url: str) -> str:
    """Raise ValueError if url has a disallowed scheme or resolves to a non-public IP.

    Returns the first vetted resolved IP address as a string. All resolved
    addresses are validated (not just the first), so any one of them is a
    valid pin for the caller to connect to without re-resolving the hostname -
    see PinnedHTTPHandler/PinnedHTTPSHandler below, which use this to close
    the DNS-rebinding TOCTOU gap (a hostname resolving to a public IP here and
    a private IP at urllib's own connect-time resolution).
    """
    parts = urlsplit(url)
    if parts.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"unsafe URL rejected: scheme {parts.scheme!r} not allowed: {url}")
    hostname = parts.hostname
    if not hostname:
        raise ValueError(f"unsafe URL rejected: no hostname: {url}")
    vetted_ip: str | None = None
    for info in socket.getaddrinfo(hostname, None):
        ip = ipaddress.ip_address(info[4][0])
        # is_global covers loopback/link-local/private/unspecified AND CGNAT
        # (100.64.0.0/10, RFC 6598), which the original explicit OR list
        # missed. It does NOT reliably cover multicast or every reserved IPv6
        # range (e.g. 4000::1 is is_reserved=True and is_global=True at once
        # in Python 3.11), so those two stay explicit belt-and-suspenders.
        if not ip.is_global or ip.is_multicast or ip.is_reserved:
            raise ValueError(f"unsafe URL rejected: non-public address {ip} for {url}")
        if any(ip in net for net in _SPECIAL_USE_DENYLIST):
            raise ValueError(f"unsafe URL rejected: special-use address {ip} for {url}")
        if vetted_ip is None:
            vetted_ip = str(ip)
    return vetted_ip


class GuardedRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: PLR0913 (stdlib signature)
        check_url_safety(newurl)
        new_request = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_request is not None and not _same_origin(req.full_url, newurl):
            for header_name in list(new_request.headers):
                if header_name.lower() not in _SAFE_REDIRECT_HEADERS:
                    del new_request.headers[header_name]
        return new_request


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection that connects to a pre-vetted IP instead of re-resolving
    self.host, while leaving self.host as the original hostname (used for the
    Host header and, in the HTTPS subclass below, SNI/certificate checks)."""

    def __init__(self, *args, pinned_ip: str, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.pinned_ip = pinned_ip

    def connect(self) -> None:
        self.sock = self._create_connection(
            (self.pinned_ip, self.port), self.timeout, self.source_address
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Like _PinnedHTTPConnection, but wraps the pinned socket with TLS using
    self.host (the original hostname, never the pinned IP) for SNI and
    certificate hostname verification - the pin only changes which address
    the TCP socket connects to."""

    def __init__(self, *args, pinned_ip: str, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.pinned_ip = pinned_ip

    def connect(self) -> None:
        self.sock = self._create_connection(
            (self.pinned_ip, self.port), self.timeout, self.source_address
        )
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


def _is_proxied(req) -> bool:  # noqa: ANN001 - urllib.request.Request, no public type alias
    """True once a ProxyHandler has already rewritten req.host to the proxy's
    host:port. ProxyHandler.proxy_open() mutates req.host but leaves
    req.full_url (the original target) untouched, so comparing the two
    detects the rewrite regardless of what the proxy's address is."""
    return req.host != urlsplit(req.full_url).netloc


class PinnedHTTPHandler(HTTPHandler):
    # ponytail: this re-validates + re-resolves the same url that
    # UrllibSourceTransport._fetch_sync already validated once before
    # building the Request. Two resolutions of the INITIAL url is accepted
    # (a few ms) - it is not a TOCTOU gap, because this resolution is the one
    # whose result gets pinned and used for the connect() that immediately
    # follows, with no unvetted resolution in between.
    def http_open(self, req):
        if _is_proxied(req):
            # ponytail: pinning depends on doing our own DNS resolution and
            # connecting straight to that IP - through a proxy, the proxy
            # does the resolution/connect instead, and our connect()
            # override would reuse the proxy's port with the target's IP,
            # silently breaking or bypassing the proxy. Skipping pinning
            # here shifts the DNS-rebinding TOCTOU concern to the proxy -
            # accepted, not fixed, because axon's expansion collector has no
            # proxy configuration today (no HTTP_PROXY/HTTPS_PROXY/
            # ProxyHandler references anywhere in src/axon/expansion or
            # docs/decisions).
            return super().http_open(req)
        ip = check_url_safety(req.full_url)
        return self.do_open(functools.partial(_PinnedHTTPConnection, pinned_ip=ip), req)


class PinnedHTTPSHandler(HTTPSHandler):
    def https_open(self, req):
        if _is_proxied(req):
            # ponytail: see PinnedHTTPHandler.http_open for why pinning is
            # skipped when proxied.
            return super().https_open(req)
        ip = check_url_safety(req.full_url)
        # Only `context` is passed through: on Python 3.12,
        # http.client.HTTPSConnection.__init__ no longer accepts a
        # check_hostname kwarg at all (TypeError), and HTTPSHandler no
        # longer exposes self._check_hostname (AttributeError) - it already
        # applies any explicit check_hostname onto self._context.check_hostname
        # when building the context, so the context alone governs
        # cert/hostname verification on both 3.11 and 3.12.
        return self.do_open(
            functools.partial(_PinnedHTTPSConnection, pinned_ip=ip, context=self._context),
            req,
        )


def build_guarded_opener() -> OpenerDirector:
    # ProxyHandler({}) disables build_opener's default proxy discovery from
    # HTTP_PROXY/HTTPS_PROXY: an ambient proxy env var would otherwise route the
    # fetch through a proxy that does its own unpinned DNS resolution + connect,
    # bypassing the IP-pinning guard entirely (Codex cross-review of PR #95).
    return build_opener(
        ProxyHandler({}),
        GuardedRedirectHandler(),
        PinnedHTTPHandler(),
        PinnedHTTPSHandler(),
    )
