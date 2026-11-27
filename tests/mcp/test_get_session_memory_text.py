"""#187: reading session_id must not change what the MCP tool prints."""
from __future__ import annotations

from datetime import UTC, datetime

from axon.mcp import server
from axon.store.session_store import SessionMemory

# recorded at f898c82d / c05edf5 for the same rows
GOLDEN = (
    "## Sessões anteriores — p\n"
    "\n"
    "**2026-01-02 03:04:05+00:00** (4 turns)\n"
    "digest of the newer session\n"
    "\n"
    "**2026-01-01 03:04:05+00:00** (0 turns)\n"
    "compact summary of the older one\n"
)


class _Store:
    async def init(self) -> None:
        return None

    async def get_session_memories(self, project: str, limit: int = 3):
        return [
            SessionMemory(
                id=2,
                project=project,
                summary="digest of the newer session",
                raw_turns=4,
                created_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
                session_id="s-2",
            ),
            SessionMemory(
                id=1,
                project=project,
                summary="compact summary of the older one",
                raw_turns=0,
                created_at=datetime(2026, 1, 1, 3, 4, 5, tzinfo=UTC),
            ),
        ]

    async def get_notes(self, project: str, limit: int = 10):
        return []

    async def find_decisions_by_repo(self, repo: str, limit: int = 10):
        return []


async def test_output_text_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(server, "_get_session_store", lambda: _Store())

    assert await server.get_session_memory("p") == GOLDEN
