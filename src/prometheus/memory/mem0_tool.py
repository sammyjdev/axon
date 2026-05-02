"""MCP tool: get_memory(query, ctx) with work context barrier."""

from __future__ import annotations

import logging
import os
from typing import Any

from prometheus.memory.config import Mem0Config

logger = logging.getLogger(__name__)

# Contexts that require explicit authorization to access
_PROTECTED_CONTEXTS = {"work"}


def _build_client() -> Any:
    from mem0 import Memory

    cfg = Mem0Config()
    return Memory.from_config(cfg.as_mem0_config())


_client: Any | None = None


def _get_client() -> Any:
    global _client
    if _client is None:
        _client = _build_client()
    return _client


async def get_memory(
    query: str,
    ctx: str = "personal",
    user_id: str = "sammy",
) -> list[dict[str, Any]]:
    """Retrieves memories matching query, filtered by context.

    The 'work' context is protected and requires PROMETHEUS_WORK_CTX=1 env var.
    """
    if ctx in _PROTECTED_CONTEXTS and os.environ.get("PROMETHEUS_WORK_CTX") != "1":
        logger.warning("Access to '%s' context denied — set PROMETHEUS_WORK_CTX=1 to enable", ctx)
        return []

    client = _get_client()
    results = client.search(query=query, user_id=user_id, limit=10)

    # Filter by context tag if stored in metadata
    return [r for r in results if r.get("metadata", {}).get("ctx", "personal") == ctx]


async def add_memory(content: str, ctx: str = "personal", user_id: str = "sammy") -> str:
    """Adds a memory with context tag."""
    if ctx in _PROTECTED_CONTEXTS and os.environ.get("PROMETHEUS_WORK_CTX") != "1":
        raise PermissionError(f"Context '{ctx}' requires PROMETHEUS_WORK_CTX=1")

    client = _get_client()
    result = client.add(content, user_id=user_id, metadata={"ctx": ctx})
    return result.get("id", "")
