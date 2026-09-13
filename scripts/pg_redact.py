"""Postgres connection URL redaction for maintenance scripts."""

from __future__ import annotations

import urllib.parse

#: Query parameters that carry a credential. libpq and asyncpg both read these
#: from the URI, so a DSN can hold a secret outside the userinfo section.
_CREDENTIAL_PARAMS = {"password", "passfile", "sslpassword", "sslkey"}


def redact_pg_url(url: str) -> str:
    """Redact credentials from a Postgres URL for safe error reporting.

    Preserves scheme, host, port, path, fragment and username, but strips the
    password so connection failures do not leak credentials. A password can also
    travel as a query parameter, which asyncpg accepts, so credential-bearing
    parameters are dropped and the rest of the query is kept.

    Fails CLOSED: a DSN this cannot parse is replaced, never echoed back. It used
    to return its input when urlsplit found no scheme or netloc, which handed the
    password straight to stderr for exactly the malformed inputs a typo produces.
    """
    try:
        parsed = urllib.parse.urlsplit(url)
        if not parsed.scheme or not parsed.netloc:
            return "<redacted-url>"
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        if parsed.username:
            host = f"{parsed.username}@{host}"
        query = urllib.parse.urlencode(
            [
                (k, v)
                for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
                if k.lower() not in _CREDENTIAL_PARAMS
            ]
        )
        return urllib.parse.urlunsplit(
            (parsed.scheme, host, parsed.path, query, parsed.fragment)
        )
    except Exception:
        return "<redacted-url>"
