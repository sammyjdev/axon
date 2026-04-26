from __future__ import annotations

import asyncio
from typing import Protocol
from urllib.request import Request, urlopen

from prometheus.expansion.models import SourceDefinition, SourceResponse


class SourceTransport(Protocol):
    async def fetch(self, url: str, source: SourceDefinition | None = None) -> SourceResponse:
        ...


class UrllibSourceTransport:
    def __init__(self, timeout_seconds: float = 10.0) -> None:
        self.timeout_seconds = timeout_seconds

    async def fetch(self, url: str, source: SourceDefinition | None = None) -> SourceResponse:
        headers = {"User-Agent": "PrometheusExpansion/1.0"}
        if source:
            headers.update(dict(source.headers))
        return await asyncio.to_thread(self._fetch_sync, url, headers)

    def _fetch_sync(self, url: str, headers: dict[str, str]) -> SourceResponse:
        request = Request(url, headers=headers)
        with urlopen(request, timeout=self.timeout_seconds) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            body = response.read().decode(charset, errors="replace")
            return SourceResponse(
                url=response.geturl(),
                status_code=response.status,
                text=body,
                content_type=response.headers.get("Content-Type"),
            )
