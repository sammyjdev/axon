from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SourceFormat(StrEnum):
    RSS = "rss"
    ATOM = "atom"
    JSON = "json"


class ExtractionMode(StrEnum):
    FEED = "feed"
    ARTICLE = "article"


@dataclass(frozen=True)
class JsonFieldMap:
    title: tuple[str | int, ...]
    url: tuple[str | int, ...]
    published_at: tuple[str | int, ...] = ()
    summary: tuple[str | int, ...] = ()
    content: tuple[str | int, ...] = ()


@dataclass(frozen=True)
class SourceDefinition:
    source_id: str
    name: str
    endpoint: str
    format: SourceFormat
    allowed_contexts: tuple[str, ...] = ("knowledge", "career", "personal")
    extraction_mode: ExtractionMode = ExtractionMode.FEED
    follow_links: bool = False
    json_items_path: tuple[str | int, ...] = ()
    json_fields: JsonFieldMap | None = None
    headers: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class SourceResponse:
    url: str
    status_code: int
    text: str
    content_type: str | None = None


@dataclass(frozen=True)
class SourceDocument:
    document_id: str
    source_id: str
    source_name: str
    title: str
    source_url: str
    published_at: str | None
    summary: str
    content: str
    metadata: dict[str, str] = field(default_factory=dict)
