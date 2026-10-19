"""Conversation search, rendered for the CLI and MCP (SQL stays in the repository)."""

from __future__ import annotations

from axon.activity.repository import PostgresActivityRepository

SNIPPET_CHARS = 160


def snippet(text: str, query: str, width: int = SNIPPET_CHARS) -> str:
    """The stretch of `text` around the first hit of `query`, on one line."""
    flat = " ".join(text.split())
    at = max(flat.lower().find(" ".join(query.split()).lower()), 0)
    start = max(at - width // 2, 0)
    end = min(start + width, len(flat))
    return ("..." if start else "") + flat[start:end] + ("..." if end < len(flat) else "")


async def search_conversations(
    repo: PostgresActivityRepository,
    query: str,
    *,
    project: str | None = None,
    harness: str | None = None,
    limit: int = 10,
) -> list[str]:
    hits = await repo.search_messages(query, project=project, harness=harness, limit=limit)
    return [
        f"{h['occurred_at']:%Y-%m-%d %H:%M} {h['session_id']} "
        f"[{h['project'] or '-'}/{h['harness']}] {h['role']}: {snippet(h['text'], query)}"
        for h in hits
    ]
