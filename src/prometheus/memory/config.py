"""Mem0 self-hosted configuration for Prometheus.

Uses Qdrant (vector store) + Neo4j (graph store) as Mem0 backends.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Mem0Config:
    qdrant_host: str = field(default_factory=lambda: os.environ.get("QDRANT_HOST", "localhost"))
    qdrant_port: int = field(default_factory=lambda: int(os.environ.get("QDRANT_PORT", "6333")))
    neo4j_uri: str = field(default_factory=lambda: os.environ.get("NEO4J_URI", "bolt://localhost:7687"))
    neo4j_user: str = field(default_factory=lambda: os.environ.get("NEO4J_USER", "neo4j"))
    neo4j_password: str = field(default_factory=lambda: os.environ.get("NEO4J_PASSWORD", "prometheus"))
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
            "graph_store": {
                "provider": "neo4j",
                "config": {
                    "url": self.neo4j_uri,
                    "username": self.neo4j_user,
                    "password": self.neo4j_password,
                },
            },
            "embedder": {
                "provider": "huggingface",
                "config": {"model": self.embed_model},
            },
            "version": "v1.1",
        }
