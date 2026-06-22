"""Regression: _open_file_cache must run migrations so the file_index table
exists on a fresh DB. Without it, a fresh install crashed on the first cache
query with 'OperationalError: no such table: file_index'."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest


async def test_open_file_cache_creates_file_index_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from axon.cli import pb

    db = tmp_path / "fresh.db"
    monkeypatch.setattr(pb, "_get_db_path", lambda: db)
    # This regression is specific to the SQLite 003 migration path; pin the
    # backend so it does not pick up the postgres default after the cutover flip.
    monkeypatch.setattr(
        pb, "_RUNTIME", dataclasses.replace(pb._RUNTIME, fileindex_backend="sqlite")
    )

    cache, conn = await pb._open_file_cache()
    try:
        # These would raise OperationalError: no such table: file_index if the
        # 003 migration had not been applied by _open_file_cache.
        await cache.set_entry("a/b.py", "knowledge", "deadbeef", 1, status="done")
        result = await cache.get_all_sha1s("knowledge")
        assert result == {"a/b.py": "deadbeef"}
    finally:
        await conn.close()
