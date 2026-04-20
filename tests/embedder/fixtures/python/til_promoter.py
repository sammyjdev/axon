from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass
class TilEntry:
    topic: str
    body: str
    tags: list[str]
    created_date: date


def parse_til(text: str) -> TilEntry | None:
    lines = text.strip().splitlines()
    if not lines:
        return None

    topic = lines[0].lstrip("#").strip()
    body = "\n".join(lines[1:]).strip()
    tags = re.findall(r"#(\w+)", body)
    return TilEntry(topic=topic, body=body, tags=tags, created_date=date.today())


def should_promote(entry: TilEntry) -> bool:
    word_count = len(entry.body.split())
    return word_count >= 50 and len(entry.tags) >= 1


def promote_to_howto(entry: TilEntry, output_dir: Path) -> Path:
    slug = re.sub(r"\W+", "-", entry.topic.lower()).strip("-")
    filename = f"{entry.created_date.isoformat()}-{slug}.md"
    output_path = output_dir / filename

    content = f"# {entry.topic}\n\n{entry.body}\n\n**Tags:** {', '.join(entry.tags)}\n"
    output_path.write_text(content, encoding="utf-8")
    return output_path


def list_pending_tils(daily_dir: Path) -> list[Path]:
    return sorted(daily_dir.glob("til-*.md"))
