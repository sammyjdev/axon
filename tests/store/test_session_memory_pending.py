"""SessionStore.drain_pending must replay spooled session_memory payloads
(axon #184) and must not quarantine them while the store is still down.

The natural key (project, md5(summary), raw_turns, created_at) makes replay
idempotent ONLY if created_at survives the spool round trip, so the payload's
timestamp - not a fresh one - must reach the repository.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from axon.store.session_store import SessionMemory, SessionStore


def _spool(data_root: Path, payload: dict, name: str = "session-1.json") -> Path:
    pending = data_root / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    path = pending / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _session_payload(**overrides) -> dict:
    payload = {
        "kind": "session_memory",
        "project": "axon",
        "summary": "spooled while postgres was down",
        "raw_turns": 12,
        "created_at": "2026-01-01T12:30:45+00:00",
    }
    payload.update(overrides)
    return payload


class _Repo:
    """Session repository stand-in injected as the store's _session_repo."""

    def __init__(self, exc: Exception | None = None) -> None:
        self.saved: list[SessionMemory] = []
        self._exc = exc

    async def save_session_memory(self, mem: SessionMemory) -> int:
        if self._exc is not None:
            raise self._exc
        self.saved.append(mem)
        return 1


def _store_with(repo: _Repo) -> SessionStore:
    store = SessionStore("unused")
    store._session_repo = repo
    return store


@pytest.mark.asyncio
async def test_drain_replays_session_memory_preserving_the_payloads_created_at(
    tmp_path, monkeypatch
):
    """AC3: the SessionMemory handed to the repository carries the payload's
    project, summary, raw_turns and exact created_at, and the file is removed
    after a successful replay."""
    data_root = tmp_path / ".axon"
    monkeypatch.setenv("AXON_DATA_ROOT", str(data_root))
    _spool(data_root, _session_payload())

    repo = _Repo()
    store = _store_with(repo)

    result = await store.drain_pending()

    assert result.processed == 1
    assert len(repo.saved) == 1
    mem = repo.saved[0]
    assert isinstance(mem, SessionMemory)
    assert mem.project == "axon"
    assert mem.summary == "spooled while postgres was down"
    assert mem.raw_turns == 12
    # A fresh timestamp here would break the ON CONFLICT natural key and
    # re-insert the session on every drain.
    assert mem.created_at == datetime(2026, 1, 1, 12, 30, 45, tzinfo=UTC)
    assert list((data_root / "pending").glob("*.json")) == [], "replayed file must be removed"


@pytest.mark.asyncio
async def test_drain_while_the_store_is_still_down_retries_instead_of_quarantining(
    tmp_path, monkeypatch
):
    """AC4: an unreachable host raises OSError; the file must stay in
    pending/ (counted as retried), not move to pending-quarantine/."""
    data_root = tmp_path / ".axon"
    monkeypatch.setenv("AXON_DATA_ROOT", str(data_root))
    _spool(data_root, _session_payload())

    repo = _Repo(exc=OSError(61, "Connect call failed"))
    store = _store_with(repo)

    result = await store.drain_pending()

    assert result.retried == 1
    assert result.quarantined == 0
    assert len(list((data_root / "pending").glob("*.json"))) == 1
    quarantine = data_root / "pending-quarantine"
    assert not quarantine.exists() or list(quarantine.iterdir()) == []


@pytest.mark.asyncio
async def test_unknown_kind_is_still_quarantined(tmp_path, monkeypatch):
    data_root = tmp_path / ".axon"
    monkeypatch.setenv("AXON_DATA_ROOT", str(data_root))
    _spool(data_root, {"kind": "mystery", "project": "axon"})

    repo = _Repo()
    result = await _store_with(repo).drain_pending()

    assert result.quarantined == 1
    assert result.processed == 0
    assert repo.saved == []
    assert list((data_root / "pending").glob("*.json")) == []
    assert list((data_root / "pending-quarantine").iterdir())


@pytest.mark.asyncio
async def test_malformed_json_is_still_quarantined(tmp_path, monkeypatch):
    data_root = tmp_path / ".axon"
    monkeypatch.setenv("AXON_DATA_ROOT", str(data_root))
    (data_root / "pending").mkdir(parents=True)
    (data_root / "pending" / "session-bad.json").write_text("{not json", encoding="utf-8")

    result = await _store_with(_Repo()).drain_pending()

    assert result.quarantined == 1
    assert list((data_root / "pending").glob("*.json")) == []


@pytest.mark.asyncio
async def test_session_memory_missing_fields_is_quarantined_not_retried(tmp_path, monkeypatch):
    """A payload that cannot be rebuilt into a SessionMemory is not
    retryable: a retryable blanket would strand it in pending/ forever."""
    data_root = tmp_path / ".axon"
    monkeypatch.setenv("AXON_DATA_ROOT", str(data_root))
    _spool(data_root, {"kind": "session_memory", "project": "axon"})

    result = await _store_with(_Repo()).drain_pending()

    assert result.quarantined == 1
    assert result.retried == 0
    assert list((data_root / "pending").glob("*.json")) == []
