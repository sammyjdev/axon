"""Tests for the SQLite migration runner in SessionStore."""

from __future__ import annotations

from pathlib import Path

import aiosqlite

from axon.store.session_store import SessionStore


async def _table_names(db_path: Path) -> set[str]:
    async with aiosqlite.connect(db_path) as db:
        rows = await db.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    return {row[0] for row in rows}


async def _applied_versions(db_path: Path) -> list[str]:
    async with aiosqlite.connect(db_path) as db:
        rows = await db.execute_fetchall("SELECT version FROM schema_version")
    return [row[0] for row in rows]


async def test_init_applies_all_migrations(tmp_path: Path) -> None:
    db_path = tmp_path / "axon.db"
    store = SessionStore(db_path=db_path)
    await store.init()
    await store.close()

    tables = await _table_names(db_path)
    # 000_baseline
    assert {"adr", "session_memory", "code_change", "session_note"} <= tables
    # 001_axon_graph
    assert {"nodes", "edges", "sessions", "commits", "decisions"} <= tables
    assert "schema_version" in tables


async def test_schema_version_records_each_migration(tmp_path: Path) -> None:
    db_path = tmp_path / "axon.db"
    store = SessionStore(db_path=db_path)
    await store.init()
    await store.close()

    assert sorted(await _applied_versions(db_path)) == [
        "000_baseline",
        "001_axon_graph",
        "002_unique_edges",
    ]


async def test_init_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "axon.db"
    store = SessionStore(db_path=db_path)
    await store.init()
    await store.init()  # second call must not error or re-apply
    await store.close()

    versions = await _applied_versions(db_path)
    assert len(versions) == len(set(versions)) == 3
