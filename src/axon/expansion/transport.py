from __future__ import annotations

import asyncio
from typing import Protocol
from urllib.request import Request

from axon.expansion.models import SourceDefinition, SourceResponse
from axon.expansion.url_safety import build_guarded_opener, check_url_safety

_OPENER = build_guarded_opener()


class SourceTransport(Protocol):
    async def fetch(self, url: str, source: SourceDefinition | None = None) -> SourceResponse: ...


class UrllibSourceTransport:
    def __init__(self, timeout_seconds: float = 10.0) -> None:
        self.timeout_seconds = timeout_seconds

    async def fetch(self, url: str, source: SourceDefinition | None = None) -> SourceResponse:
        headers = {"User-Agent": "AxonExpansion/1.0"}
        if source:
            headers.update(dict(source.headers))
        return await asyncio.to_thread(self._fetch_sync, url, headers)

    def _fetch_sync(self, url: str, headers: dict[str, str]) -> SourceResponse:
        check_url_safety(url)
        request = Request(url, headers=headers)
        # guarded by check_url_safety (scheme allowlist + private-IP block);
        # redirects validated via GuardedRedirectHandler
        with _OPENER.open(request, timeout=self.timeout_seconds) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            body = response.read().decode(charset, errors="replace")
            return SourceResponse(
                url=response.geturl(),
                status_code=response.status,
                text=body,
                content_type=response.headers.get("Content-Type"),
            )
