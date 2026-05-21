"""Typed exception hierarchy for AXON.

Every AXON error carries an optional ``context`` mapping for debugging; it is
rendered into ``str()`` so it surfaces in logs and tracebacks.
"""

from __future__ import annotations

from typing import Any


class AxonError(Exception):
    """Base class for all AXON errors."""

    def __init__(self, message: str = "", **context: Any) -> None:
        self.message = message
        self.context: dict[str, Any] = context
        super().__init__(message)

    def __str__(self) -> str:
        if not self.context:
            return self.message
        rendered = ", ".join(f"{k}={v!r}" for k, v in self.context.items())
        return f"{self.message} ({rendered})" if self.message else f"({rendered})"


class ObsidianError(AxonError):
    """Vault discovery, read, or export failure."""


class GraphError(AxonError):
    """Graph store (SQLite subgraph / Redis cache) failure."""


class CodeIndexError(AxonError):
    """Code indexing, chunking, or symbol-resolution failure."""


class GitAnchorError(AxonError):
    """Git hook, event, or anchor-resolution failure."""


class MCPError(AxonError):
    """MCP server tool or transport failure."""


class ValidationError(AxonError):
    """Domain validation failure (distinct from ``pydantic.ValidationError``)."""
