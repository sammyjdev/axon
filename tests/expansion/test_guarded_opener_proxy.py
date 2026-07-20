from __future__ import annotations

from urllib.request import ProxyHandler

import pytest

from axon.expansion.url_safety import build_guarded_opener


def test_guarded_opener_ignores_env_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    # build_opener() auto-installs a ProxyHandler seeded from HTTP_PROXY/HTTPS_PROXY.
    # A configured proxy performs its own (unpinned) DNS resolution + connect,
    # bypassing the IP-pinning guard entirely (Codex cross-review of PR #95, HIGH).
    # The guarded opener must NOT honor ambient proxy env vars.
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.internal:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:8080")

    opener = build_guarded_opener()

    # The security property: no active proxy handler routes the fetch to a
    # proxy. Passing ProxyHandler({}) makes build_opener drop the default
    # env-seeded ProxyHandler and register no proxy handling at all, so the
    # list is empty; without the fix a default ProxyHandler seeded from
    # HTTP_PROXY would be present with a non-empty `proxies` dict.
    proxy_handlers = [h for h in opener.handlers if isinstance(h, ProxyHandler)]
    assert all(h.proxies == {} for h in proxy_handlers), (
        f"guarded opener honored env proxies: {[h.proxies for h in proxy_handlers]}"
    )
