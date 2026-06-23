"""Tests for SessionStore WAL pragma + retry + pending fallback (dec-112)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from axon.store.session_store import ADR, CodeChange, SessionStore


@pytest.mark.asyncio
class TestSessionStoreWAL:
    async def test_journal_mode_is_wal(self, tmp_path: Path) -> None:
        store = SessionStore(db_path=tmp_path / "wal.db")
        await store.init()
        db = await store._connection()
        cursor = await db.execute("PRAGMA journal_mode")
        row = await cursor.fetchone()
        assert row is not None
        assert row[0].lower() == "wal"
        await store.close()

    async def test_busy_timeout_set(self, tmp_path: Path) -> None:
        store = SessionStore(db_path=tmp_path / "to.db")
        await store.init()
        db = await store._connection()
        cursor = await db.execute("PRAGMA busy_timeout")
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] >= 5000
        await store.close()

    async def test_synchronous_is_normal(self, tmp_path: Path) -> None:
        store = SessionStore(db_path=tmp_path / "sync.db")
        await store.init()
        db = await store._connection()
        cursor = await db.execute("PRAGMA synchronous")
        row = await cursor.fetchone()
        assert row is not None
        # synchronous=NORMAL is 1
        assert row[0] == 1
        await store.close()


@pytest.mark.asyncio
class TestSessionStoreDrain:
    async def test_drain_pending_writes_code_change_to_db(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from axon.store.pending import PendingPaths, write_pending

        pending_root = tmp_path / "axon_data"
        monkeypatch.setenv("AXON_DATA_ROOT", str(pending_root))

        store = SessionStore(db_path=tmp_path / "drain.db")
        await store.init()

        # Simulate a previous fallback by writing directly to pending/
        paths = PendingPaths(
            pending_dir=pending_root / "pending",
            quarantine_dir=pending_root / "pending-quarantine",
            quarantine_log=pending_root / "quarantine.jsonl",
        )
        await write_pending(
            payload={
                "kind": "code_change",
                "commit_hash": "cafebabe",
                "file_path": "x.py",
                "diff_summary": "..",
                "why": "",
                "changed_at": "2026-05-27T00:00:00+00:00",
            },
            commit_hash="cafebabe",
            paths=paths,
        )

        result = await store.drain_pending()
        assert result.processed == 1
        assert result.quarantined == 0

        changes = await store.get_recent_changes("x.py")
        assert len(changes) == 1
        assert changes[0].commit_hash == "cafebabe"
        await store.close()


@pytest.mark.asyncio
class TestSessionStorePendingFallback:
    async def test_save_code_change_writes_to_pending_when_db_unwritable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When DB write raises an unretryable-after-budget error, the payload
        must land in `.axon/pending/` and the call must still return success.

        After MS-6: the pending fallback lives in SqliteSessionRepository.save_code_change.
        SessionStore.save_code_change is a thin delegator, so we patch the repo layer.
        """
        from axon.store.session_repository import SqliteSessionRepository

        pending_root = tmp_path / "axon_data"
        monkeypatch.setenv("AXON_DATA_ROOT", str(pending_root))
        monkeypatch.setenv("AXON_SESSIONS_BACKEND", "sqlite")

        store = SessionStore(db_path=tmp_path / "fail.db")
        await store.init()

        # Force every DB save attempt to raise "database is locked"
        async def boom(self, *a, **kw):  # noqa: ANN001
            import aiosqlite
            raise aiosqlite.OperationalError("database is locked")

        monkeypatch.setattr(SqliteSessionRepository, "save_code_change_inner", boom)

        change = CodeChange(
            commit_hash="deadbeef",
            file_path="src/x.py",
            diff_summary="...",
        )
        # Should NOT raise — falls back to pending
        await store.save_code_change(change)

        pending_dir = pending_root / "pending"
        assert pending_dir.exists()
        files = list(pending_dir.glob("deadbeef-*.json"))
        assert len(files) == 1
        payload = json.loads(files[0].read_text())
        assert payload["kind"] == "code_change"
        assert payload["commit_hash"] == "deadbeef"

        # Capture warning emitted
        warnings_log = pending_root / "capture-warnings.jsonl"
        assert warnings_log.exists()
        lines = warnings_log.read_text().strip().splitlines()
        assert len(lines) >= 1
        entry = json.loads(lines[-1])
        assert entry["kind"] == "code_change"
        assert entry["commit_hash"] == "deadbeef"

        await store.close()

    async def test_save_adr_writes_to_pending_on_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from axon.store import session_store as ss_module

        pending_root = tmp_path / "axon_data"
        monkeypatch.setenv("AXON_DATA_ROOT", str(pending_root))

        store = SessionStore(db_path=tmp_path / "fail.db")
        await store.init()

        async def boom(self, *a, **kw):  # noqa: ANN001
            import aiosqlite
            raise aiosqlite.OperationalError("database is locked")

        monkeypatch.setattr(ss_module.SessionStore, "_save_adr_inner", boom)

        adr = ADR(
            project="p",
            title="t",
            context="c",
            decision="d",
            rationale="r",
        )
        result = await store.save_adr(adr)
        # On fallback, return value is 0 (no DB row id)
        assert result == 0

        pending_dir = pending_root / "pending"
        files = list(pending_dir.glob("*.json"))
        # ADR has no commit_hash, so filename uses a synthetic key
        assert len(files) == 1
        payload = json.loads(files[0].read_text())
        assert payload["kind"] == "adr"
        assert payload["project"] == "p"

        await store.close()
