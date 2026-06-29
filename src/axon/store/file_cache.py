# src/axon/store/file_cache.py
"""Persistent file-hash cache backed by the file_index table.

FileCache is a Protocol so tests can inject mocks. PostgresFileCache
(``pg_file_cache.py``) is the only production implementation.

All file_path values are normalized to posix form before storage so that
Windows backslash paths and posix slash paths produce identical lookup keys.
"""
from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from axon.config.runtime import RuntimeConfig


class FileCache(Protocol):
    async def get_all_sha1s(self, ctx: str) -> dict[str, str]:
        """Return {file_path_posix: sha1} for all 'done' entries in ctx.

        Uses a single SELECT. Pending rows (crash sentinels) are excluded -
        they are treated as hash misses and trigger a full re-index.
        """
        ...

    async def set_entry(
        self,
        file_path: str,
        ctx: str,
        sha1: str,
        chunk_count: int,
        *,
        status: str = "done",
    ) -> None:
        """Insert or update a file_index row. Use status='pending' before
        vector-store mutation; status='done' only after _flush_batch() succeeds.
        """
        ...

    async def delete_entry(self, file_path: str, ctx: str) -> None:
        """Remove a file_index entry (used when file is deleted from repo)."""
        ...

    async def list_entries(self, ctx: str) -> list[tuple[str, str]]:
        """Return [(file_path_posix, sha1)] for ALL entries in ctx (any status).

        Used to detect files removed from the repo (compare against walk result).
        """
        ...


def sha1_of_source(source: str) -> str:
    """SHA-1 of UTF-8 encoded source content.

    MUST remain identical to pipeline.py:
        hashlib.sha1(source.encode("utf-8")).hexdigest()
    Any change here requires a matching change in pipeline.py AND
    a documented one-time cold-start full re-embed.

    Does not pass usedforsecurity kwarg to match pipeline.py exactly.
    """
    return hashlib.sha1(source.encode("utf-8")).hexdigest()


async def make_file_cache(runtime: RuntimeConfig) -> tuple[FileCache, object]:
    """Build the file-hash cache for the active ``fileindex_backend``.

    Returns ``(cache, closer)``; await ``closer.close()`` when finished (for the
    Postgres backend ``closer`` is the cache itself, which closes its pool).
    Honours the backend switch so callers never hard-wire SQLite.
    """
    from axon.store.pg_file_cache import PostgresFileCache

    cache = PostgresFileCache(dsn=runtime.pg_url)
    await cache.ensure_schema()
    return cache, cache
