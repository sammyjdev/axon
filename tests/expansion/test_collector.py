from __future__ import annotations

import pytest

from prometheus.expansion import (
    ExpansionCollector,
    JsonFieldMap,
    SourceDefinition,
    SourceFormat,
    SourceRegistry,
    SourceResponse,
    UnknownSourceError,
)
from prometheus.expansion.registry import load_source_registry


class FakeTransport:
    def __init__(self, responses: dict[str, str]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    async def fetch(self, url: str, source: SourceDefinition | None = None) -> SourceResponse:
        _ = source
        self.calls.append(url)
        return SourceResponse(
            url=url,
            status_code=200,
            text=self.responses[url],
            content_type="text/plain",
        )


@pytest.mark.asyncio
async def test_collect_rss_source_with_article_follow_extracts_deterministic_content() -> None:
    feed = """<?xml version="1.0"?>
    <rss version="2.0">
      <channel>
        <item>
          <title>Beta</title>
          <link>https://example.com/beta</link>
          <description><![CDATA[<p>Resumo beta</p>]]></description>
          <pubDate>Tue, 23 Apr 2026 15:30:00 +0000</pubDate>
        </item>
        <item>
          <title>Alpha</title>
          <link>https://example.com/alpha</link>
          <description><![CDATA[<p>Resumo alpha</p>]]></description>
          <pubDate>Tue, 22 Apr 2026 10:00:00 +0000</pubDate>
        </item>
      </channel>
    </rss>
    """
    responses = {
        "https://feeds.example.com/rss.xml": feed,
        "https://example.com/alpha": (
            "<html><body><article><h1>Alpha</h1>"
            "<p>Conteudo final alpha.</p></article></body></html>"
        ),
        "https://example.com/beta": (
            "<html><body><main><p>Conteudo final beta.</p></main></body></html>"
        ),
    }
    registry = SourceRegistry(
        (
            SourceDefinition(
                source_id="example-feed",
                name="Example Feed",
                endpoint="https://feeds.example.com/rss.xml",
                format=SourceFormat.RSS,
                follow_links=True,
            ),
        )
    )

    collector = ExpansionCollector(registry=registry, transport=FakeTransport(responses))
    documents = await collector.collect("example-feed")

    assert [document.title for document in documents] == ["Alpha", "Beta"]
    assert documents[0].source_url == "https://example.com/alpha"
    assert documents[0].published_at == "2026-04-22T10:00:00Z"
    assert documents[0].summary == "Resumo alpha"
    assert documents[0].content == "Alpha Conteudo final alpha."
    assert documents[1].source_url == "https://example.com/beta"
    assert documents[1].published_at == "2026-04-23T15:30:00Z"
    assert documents[1].content == "Conteudo final beta."


@pytest.mark.asyncio
async def test_collect_json_source_uses_registered_mapping_and_deduplicates() -> None:
    payload = """
    {
      "items": [
        {
          "headline": "Carreira B",
          "permalink": "https://career.example.com/b",
          "published": "2026-04-23T09:15:00Z",
          "summary": "<p>Resumo b</p>",
          "body": "<div>Conteudo b</div>"
        },
        {
          "headline": "Carreira A",
          "permalink": "https://career.example.com/a",
          "published": "2026-04-21",
          "summary": "<p>Resumo a</p>",
          "body": "<div>Conteudo a</div>"
        },
        {
          "headline": "Carreira A",
          "permalink": "https://career.example.com/a",
          "published": "2026-04-21",
          "summary": "<p>Resumo duplicado</p>",
          "body": "<div>Conteudo a</div>"
        }
      ]
    }
    """
    registry = SourceRegistry(
        (
            SourceDefinition(
                source_id="career-api",
                name="Career API",
                endpoint="https://career.example.com/api/items",
                format=SourceFormat.JSON,
                json_items_path=("items",),
                json_fields=JsonFieldMap(
                    title=("headline",),
                    url=("permalink",),
                    published_at=("published",),
                    summary=("summary",),
                    content=("body",),
                ),
            ),
        )
    )

    collector = ExpansionCollector(
        registry=registry,
        transport=FakeTransport({"https://career.example.com/api/items": payload}),
    )
    documents = await collector.collect("career-api")

    assert [document.title for document in documents] == ["Carreira A", "Carreira B"]
    assert documents[0].source_url == "https://career.example.com/a"
    assert documents[0].published_at == "2026-04-21T00:00:00"
    assert documents[0].summary == "Resumo a"
    assert documents[0].content == "Conteudo a"
    assert documents[1].source_url == "https://career.example.com/b"
    assert documents[1].published_at == "2026-04-23T09:15:00Z"


def test_registry_lists_sources_only_for_allowed_context() -> None:
    registry = SourceRegistry(
        (
            SourceDefinition(
                source_id="career-feed",
                name="Career Feed",
                endpoint="https://career.example.com/rss.xml",
                format=SourceFormat.RSS,
                allowed_contexts=("career",),
            ),
            SourceDefinition(
                source_id="knowledge-feed",
                name="Knowledge Feed",
                endpoint="https://knowledge.example.com/rss.xml",
                format=SourceFormat.RSS,
                allowed_contexts=("knowledge", "career"),
            ),
        )
    )

    assert [source.source_id for source in registry.list_for_context("career")] == [
        "career-feed",
        "knowledge-feed",
    ]
    assert [source.source_id for source in registry.list_for_context("knowledge")] == [
        "knowledge-feed",
    ]
    assert registry.list_for_context("work") == []


@pytest.mark.asyncio
async def test_collect_rejects_unregistered_source() -> None:
    collector = ExpansionCollector(registry=SourceRegistry(()), transport=FakeTransport({}))

    with pytest.raises(UnknownSourceError):
        await collector.collect("missing-source")


def test_load_source_registry_from_json_catalog(tmp_path) -> None:
    catalog = tmp_path / "expansion_sources.json"
    catalog.write_text(
        """
        {
          "sources": [
            {
              "source_id": "knowledge-feed",
              "name": "Knowledge Feed",
              "endpoint": "https://knowledge.example.com/rss.xml",
              "format": "rss",
              "allowed_contexts": ["knowledge", "career"],
              "follow_links": true
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    registry = load_source_registry(catalog)

    assert [source.source_id for source in registry.list_for_context("knowledge")] == [
        "knowledge-feed"
    ]
    assert registry.get("knowledge-feed").follow_links is True
