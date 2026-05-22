"""Mem0 self-hosted configuration for AXON.

Uses Qdrant as the Mem0 vector backend, local-only. There is no graph store —
see docs/decisions/dec-101-revoke-d4-drop-neo4j.md.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlparse


def _qdrant_host() -> str:
    parsed = urlparse(os.environ.get("QDRANT_URL", "http://localhost:6333"))
    if parsed.hostname:
        return parsed.hostname
    if host := os.environ.get("QDRANT_HOST"):
        return host
    return parsed.hostname or "localhost"


def _qdrant_port() -> int:
    parsed = urlparse(os.environ.get("QDRANT_URL", "http://localhost:6333"))
    if parsed.port:
        return parsed.port
    if port := os.environ.get("QDRANT_PORT"):
        return int(port)
    return parsed.port or 6333


@dataclass
class Mem0Config:
    qdrant_host: str = field(default_factory=_qdrant_host)
    qdrant_port: int = field(default_factory=_qdrant_port)
    collection_name: str = "mem0_memories"
    # Embed model for Mem0 (lightweight for speed)
    embed_model: str = "BAAI/bge-small-en-v1.5"

    def as_mem0_config(self) -> dict:
        """Returns a mem0-compatible config dict."""
        return {
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "host": self.qdrant_host,
                    "port": self.qdrant_port,
                    "collection_name": self.collection_name,
                    "embedding_model_dims": 384,
                },
            },
            "embedder": {
                "provider": "huggingface",
                "config": {"model": self.embed_model},
            },
            "version": "v1.1",
        }
