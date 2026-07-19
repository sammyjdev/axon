from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, OpenerDirector, build_opener

_ALLOWED_SCHEMES = frozenset({"http", "https"})


def check_url_safety(url: str) -> None:
    """Raise ValueError if url has a disallowed scheme or resolves to a non-public IP."""
    parts = urlsplit(url)
    if parts.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"unsafe URL rejected: scheme {parts.scheme!r} not allowed: {url}")
    hostname = parts.hostname
    if not hostname:
        raise ValueError(f"unsafe URL rejected: no hostname: {url}")
    # ponytail: DNS-rebinding TOCTOU accepted - this resolves once here, urllib resolves
    # again at connect time. No IP pinning requested by the issue; out of scope.
    for info in socket.getaddrinfo(hostname, None):
        ip = ipaddress.ip_address(info[4][0])
        # is_global covers loopback/link-local/private/unspecified AND CGNAT
        # (100.64.0.0/10, RFC 6598), which the original explicit OR list
        # missed. It does NOT reliably cover multicast or every reserved IPv6
        # range (e.g. 4000::1 is is_reserved=True and is_global=True at once
        # in Python 3.11), so those two stay explicit belt-and-suspenders.
        if not ip.is_global or ip.is_multicast or ip.is_reserved:
            raise ValueError(f"unsafe URL rejected: non-public address {ip} for {url}")


class GuardedRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: PLR0913 (stdlib signature)
        check_url_safety(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def build_guarded_opener() -> OpenerDirector:
    return build_opener(GuardedRedirectHandler())
