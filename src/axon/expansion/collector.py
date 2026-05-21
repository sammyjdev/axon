from __future__ import annotations

from dataclasses import dataclass

from axon.expansion.extractors import extract_documents, resolve_article_urls
from axon.expansion.models import SourceDefinition, SourceDocument
from axon.expansion.registry import SourceRegistry
from axon.expansion.transport import SourceTransport, UrllibSourceTransport


@dataclass
class ExpansionCollector:
    registry: SourceRegistry
    transport: SourceTransport | None = None

    async def collect(self, source_id: str) -> list[SourceDocument]:
        source = self.registry.get(source_id)
        transport = self.transport or UrllibSourceTransport()
        source_response = await transport.fetch(source.endpoint, source)
        article_payloads = await self._fetch_article_payloads(
            transport,
            source,
            source_response.text,
        )
        return extract_documents(source, source_response.text, article_payloads)

    async def collect_many(self, source_ids: list[str]) -> dict[str, list[SourceDocument]]:
        return {source_id: await self.collect(source_id) for source_id in source_ids}

    async def _fetch_article_payloads(
        self,
        transport: SourceTransport,
        source: SourceDefinition,
        payload: str,
    ) -> dict[str, str]:
        urls = resolve_article_urls(source, payload)
        article_payloads: dict[str, str] = {}
        for url in urls:
            response = await transport.fetch(url, source)
            article_payloads[url] = response.text
        return article_payloads
