#!/usr/bin/env python3
"""One-shot vault indexer. Walks vault path and indexes all supported files."""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from prometheus.embedder.engine import EmbedderEngine
from prometheus.embedder.pipeline import ingest_file
from prometheus.store.vector import VectorStore

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_SUPPORTED_EXTENSIONS = {".java", ".py", ".ts"}


async def index_vault(vault_path: Path) -> None:
    engine = EmbedderEngine()
    store = VectorStore()

    files = [
        p for p in vault_path.rglob("*")
        if p.is_file() and p.suffix in _SUPPORTED_EXTENSIONS
    ]

    logger.info("Found %d files to index in %s", len(files), vault_path)
    total = 0
    for file in files:
        try:
            count = await ingest_file(file, engine, store)
            total += count
        except Exception:
            logger.exception("Failed to index %s", file)

    logger.info("Indexing complete — %d chunks total", total)


if __name__ == "__main__":
    vault = Path(os.environ.get("PROMETHEUS_VAULT", Path.home() / "vault"))
    asyncio.run(index_vault(vault))
