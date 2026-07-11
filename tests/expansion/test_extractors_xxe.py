from __future__ import annotations

import pytest
from defusedxml.common import DefusedXmlException

from axon.expansion.extractors import extract_documents, resolve_article_urls
from axon.expansion.models import SourceDefinition, SourceFormat

_MALICIOUS_RSS = """<?xml version="1.0"?>
<!DOCTYPE rss [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
]>
<rss version="2.0">
  <channel>
    <item>
      <title>&lol2;</title>
      <link>https://example.com/x</link>
      <description>desc</description>
      <pubDate>Tue, 22 Apr 2026 10:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""

_MALICIOUS_ATOM = """<?xml version="1.0"?>
<!DOCTYPE feed [
  <!ENTITY lol "lol">
]>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>&lol;</title>
    <link href="https://example.com/x" rel="alternate"/>
    <summary>desc</summary>
    <published>2026-04-22T10:00:00Z</published>
  </entry>
</feed>
"""

_VALID_RSS = """<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Alpha</title>
      <link>https://example.com/alpha</link>
      <description>Resumo alpha</description>
      <pubDate>Tue, 22 Apr 2026 10:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""

_RSS_SOURCE = SourceDefinition(
    source_id="malicious-rss",
    name="Malicious RSS",
    endpoint="https://feeds.example.com/rss.xml",
    format=SourceFormat.RSS,
)

_ATOM_SOURCE = SourceDefinition(
    source_id="malicious-atom",
    name="Malicious Atom",
    endpoint="https://feeds.example.com/atom.xml",
    format=SourceFormat.ATOM,
)

_RSS_SOURCE_FOLLOW = SourceDefinition(
    source_id="malicious-rss-follow",
    name="Malicious RSS Follow",
    endpoint="https://feeds.example.com/rss.xml",
    format=SourceFormat.RSS,
    follow_links=True,
)

_ATOM_SOURCE_FOLLOW = SourceDefinition(
    source_id="malicious-atom-follow",
    name="Malicious Atom Follow",
    endpoint="https://feeds.example.com/atom.xml",
    format=SourceFormat.ATOM,
    follow_links=True,
)


def test_extract_documents_rejects_billion_laughs_rss() -> None:
    with pytest.raises(DefusedXmlException):
        extract_documents(_RSS_SOURCE, _MALICIOUS_RSS)


def test_extract_documents_rejects_billion_laughs_atom() -> None:
    with pytest.raises(DefusedXmlException):
        extract_documents(_ATOM_SOURCE, _MALICIOUS_ATOM)


def test_resolve_article_urls_rejects_billion_laughs_rss() -> None:
    with pytest.raises(DefusedXmlException):
        resolve_article_urls(_RSS_SOURCE_FOLLOW, _MALICIOUS_RSS)


def test_resolve_article_urls_rejects_billion_laughs_atom() -> None:
    with pytest.raises(DefusedXmlException):
        resolve_article_urls(_ATOM_SOURCE_FOLLOW, _MALICIOUS_ATOM)


def test_extract_documents_still_parses_valid_rss() -> None:
    documents = extract_documents(_RSS_SOURCE, _VALID_RSS)

    assert [document.title for document in documents] == ["Alpha"]
    assert documents[0].source_url == "https://example.com/alpha"
