from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin
from xml.etree import ElementTree

from defusedxml.ElementTree import fromstring as _safe_fromstring

from axon.expansion.models import SourceDefinition, SourceDocument, SourceFormat


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        _ = attrs
        if tag in {"script", "style"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        return " ".join(self._parts)


def extract_documents(
    source: SourceDefinition,
    payload: str,
    article_payloads: dict[str, str] | None = None,
) -> list[SourceDocument]:
    article_payloads = article_payloads or {}
    if source.format is SourceFormat.RSS:
        items = _extract_rss_items(source, payload, article_payloads)
    elif source.format is SourceFormat.ATOM:
        items = _extract_atom_items(source, payload, article_payloads)
    elif source.format is SourceFormat.JSON:
        items = _extract_json_items(source, payload, article_payloads)
    else:
        raise ValueError(f"unsupported source format: {source.format}")

    deduped: dict[tuple[str, str, str | None], SourceDocument] = {}
    for item in items:
        deduped.setdefault((item.source_url, item.title, item.published_at), item)
    return sorted(
        deduped.values(),
        key=lambda item: (
            item.published_at or "",
            item.source_url,
            item.title,
        ),
    )


def _extract_rss_items(
    source: SourceDefinition,
    payload: str,
    article_payloads: dict[str, str],
) -> list[SourceDocument]:
    root = _safe_fromstring(payload)
    items: list[SourceDocument] = []
    for node in root.findall(".//item"):
        title = _normalize_text(_xml_text(node.find("title")))
        link = _xml_text(node.find("link")).strip()
        summary = _normalize_text(_xml_text(node.find("description")))
        published_at = _normalize_datetime(_xml_text(node.find("pubDate")))
        content = _resolve_content(source, link, summary, article_payloads)
        if not link or not title or not content:
            continue
        items.append(
            _build_document(
                source=source,
                title=title,
                source_url=link,
                published_at=published_at,
                summary=summary,
                content=content,
            )
        )
    return items


def _extract_atom_items(
    source: SourceDefinition,
    payload: str,
    article_payloads: dict[str, str],
) -> list[SourceDocument]:
    root = _safe_fromstring(payload)
    namespace = ""
    if root.tag.startswith("{") and "}" in root.tag:
        namespace = root.tag.split("}", 1)[0] + "}"
    items: list[SourceDocument] = []
    for node in root.findall(f".//{namespace}entry"):
        title = _normalize_text(_xml_text(node.find(f"{namespace}title")))
        summary = _normalize_text(
            _xml_text(node.find(f"{namespace}summary"))
            or _xml_text(node.find(f"{namespace}content"))
        )
        published_at = _normalize_datetime(
            _xml_text(node.find(f"{namespace}published"))
            or _xml_text(node.find(f"{namespace}updated"))
        )
        link = ""
        for link_node in node.findall(f"{namespace}link"):
            href = link_node.attrib.get("href", "").strip()
            rel = link_node.attrib.get("rel", "alternate")
            if href and rel == "alternate":
                link = href
                break
        content = _resolve_content(source, link, summary, article_payloads)
        if not link or not title or not content:
            continue
        items.append(
            _build_document(
                source=source,
                title=title,
                source_url=link,
                published_at=published_at,
                summary=summary,
                content=content,
            )
        )
    return items


def _extract_json_items(
    source: SourceDefinition,
    payload: str,
    article_payloads: dict[str, str],
) -> list[SourceDocument]:
    if source.json_fields is None:
        raise ValueError(f"json source requires field map: {source.source_id}")
    raw = json.loads(payload)
    rows = _get_path(raw, source.json_items_path)
    if not isinstance(rows, list):
        raise ValueError(f"json items path must resolve to a list: {source.source_id}")
    items: list[SourceDocument] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = _normalize_text(_string_path(row, source.json_fields.title))
        link = _string_path(row, source.json_fields.url).strip()
        summary = _normalize_text(_string_path(row, source.json_fields.summary))
        published_at = _normalize_datetime(_string_path(row, source.json_fields.published_at))
        inline_content = _normalize_text(_string_path(row, source.json_fields.content))
        content = _resolve_content(source, link, inline_content or summary, article_payloads)
        if not link or not title or not content:
            continue
        items.append(
            _build_document(
                source=source,
                title=title,
                source_url=link,
                published_at=published_at,
                summary=summary,
                content=content,
            )
        )
    return items


def _resolve_content(
    source: SourceDefinition,
    link: str,
    fallback: str,
    article_payloads: dict[str, str],
) -> str:
    if source.follow_links and link in article_payloads:
        return _extract_article_text(article_payloads[link]) or fallback
    return fallback


def _build_document(
    source: SourceDefinition,
    title: str,
    source_url: str,
    published_at: str | None,
    summary: str,
    content: str,
) -> SourceDocument:
    stable_key = "|".join([source.source_id, source_url, published_at or "", title])
    document_id = hashlib.sha256(stable_key.encode("utf-8")).hexdigest()
    return SourceDocument(
        document_id=document_id,
        source_id=source.source_id,
        source_name=source.name,
        title=title,
        source_url=source_url,
        published_at=published_at,
        summary=summary,
        content=content,
        metadata={"endpoint": source.endpoint},
    )


def _extract_article_text(payload: str) -> str:
    preferred = (
        _extract_tag(payload, "article")
        or _extract_tag(payload, "main")
        or _extract_tag(payload, "body")
    )
    return _normalize_text(preferred or payload)


def _extract_tag(payload: str, tag: str) -> str:
    pattern = re.compile(rf"<{tag}\b[^>]*>(.*?)</{tag}>", re.IGNORECASE | re.DOTALL)
    match = pattern.search(payload)
    return match.group(1) if match else ""


def _xml_text(node: ElementTree.Element | None) -> str:
    if node is None:
        return ""
    return "".join(node.itertext()).strip()


def _get_path(data: Any, path: tuple[str | int, ...]) -> Any:
    current = data
    for segment in path:
        if isinstance(segment, int):
            if not isinstance(current, list):
                return None
            try:
                current = current[segment]
            except IndexError:
                return None
            continue
        if not isinstance(current, dict):
            return None
        current = current.get(segment)
    return current


def _string_path(data: dict[str, Any], path: tuple[str | int, ...]) -> str:
    if not path:
        return ""
    value = _get_path(data, path)
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def _normalize_text(value: str) -> str:
    if not value:
        return ""
    parser = _HTMLTextExtractor()
    parser.feed(unescape(value))
    parser.close()
    raw = parser.text()
    return re.sub(r"\s+", " ", raw).strip()


def _normalize_datetime(value: str) -> str | None:
    if not value:
        return None
    raw = value.strip()
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return raw
    if parsed.tzinfo is None:
        return parsed.isoformat()
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def resolve_article_urls(
    source: SourceDefinition,
    payload: str,
) -> list[str]:
    if not source.follow_links:
        return []
    if source.format is SourceFormat.RSS:
        root = _safe_fromstring(payload)
        urls = [_xml_text(node.find("link")).strip() for node in root.findall(".//item")]
    elif source.format is SourceFormat.ATOM:
        root = _safe_fromstring(payload)
        namespace = ""
        if root.tag.startswith("{") and "}" in root.tag:
            namespace = root.tag.split("}", 1)[0] + "}"
        urls = []
        for node in root.findall(f".//{namespace}entry"):
            for link_node in node.findall(f"{namespace}link"):
                href = link_node.attrib.get("href", "").strip()
                rel = link_node.attrib.get("rel", "alternate")
                if href and rel == "alternate":
                    urls.append(urljoin(source.endpoint, href))
                    break
    elif source.format is SourceFormat.JSON:
        if source.json_fields is None:
            raise ValueError(f"json source requires field map: {source.source_id}")
        raw = json.loads(payload)
        rows = _get_path(raw, source.json_items_path)
        if not isinstance(rows, list):
            raise ValueError(f"json items path must resolve to a list: {source.source_id}")
        urls = [
            urljoin(source.endpoint, _string_path(row, source.json_fields.url).strip())
            for row in rows
            if isinstance(row, dict)
        ]
    else:
        raise ValueError(f"unsupported source format: {source.format}")
    return sorted({url for url in urls if url})
