from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SourceFormat(StrEnum):
    RSS = "rss"
    ATOM = "atom"
    JSON = "json"


class ExtractionMode(StrEnum):
    FEED = "feed"
    ARTICLE = "article"


class JsonFieldMap(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: tuple[str | int, ...]
    url: tuple[str | int, ...]
    published_at: tuple[str | int, ...] = ()
    summary: tuple[str | int, ...] = ()
    content: tuple[str | int, ...] = ()


class SourceDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

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


class SourceResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: str
    status_code: int
    text: str
    content_type: str | None = None


class SourceDocument(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: str
    source_id: str
    source_name: str
    title: str
    source_url: str
    published_at: str | None
    summary: str
    content: str
    metadata: dict[str, str] = Field(default_factory=dict)
