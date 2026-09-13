"""Postgres connection URL redaction for maintenance scripts."""

from __future__ import annotations

import urllib.parse


def redact_pg_url(url: str) -> str:
    """Redact credentials from a Postgres URL for safe error reporting.

    Preserves scheme, host, port, path, query, fragment and username, but
    strips the password so connection failures do not leak credentials.
    """
    try:
        parsed = urllib.parse.urlsplit(url)
        if not parsed.scheme or not parsed.netloc:
            return url
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        if parsed.username:
            host = f"{parsed.username}@{host}"
        return urllib.parse.urlunsplit(
            (parsed.scheme, host, parsed.path, parsed.query, parsed.fragment)
        )
    except Exception:
        return "<redacted-url>"
