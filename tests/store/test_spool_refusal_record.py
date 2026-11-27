"""A refused payload is dropped, so the refusal is recorded (axon #202).

The record lives under the engine data root, never beside the spool: a
read-only ``.axon`` is exactly the case it has to report. The suite's autouse
fixture gives every test its own ``AXON_ENGINE``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from axon.config.runtime import load_runtime_config
from axon.store import pending
from axon.store.pending import _pending_paths, write_pending
from tests.cli.test_spool_not_committable import _git

_NOW = datetime(2026, 11, 26, 12, 0, 0, tzinfo=UTC)


class _Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return _NOW


def _record_path() -> Path:
    return load_runtime_config().data_root / "spool-refusals.json"


def _unignorable_repo(tmp_path: Path, monkeypatch) -> Path:
    """A repo whose ignore file is a directory: every spool write is refused."""
    monkeypatch.setenv("AXON_DATA_ROOT", str(tmp_path / ".axon"))
    _git(tmp_path, "init")
    (tmp_path / ".axon" / ".gitignore").mkdir(parents=True)
    return tmp_path


async def _refused() -> pending.SpoolNotIgnoredError:
    with pytest.raises(pending.SpoolNotIgnoredError) as refused:
        await write_pending(
            payload={"summary": "literal user prompt"},
            commit_hash="session",
            paths=_pending_paths(),
        )
    return refused.value


async def test_each_refusal_updates_the_record(tmp_path, monkeypatch):
    """Criterion 15."""
    repo = _unignorable_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(pending, "datetime", _Clock)
    assert not _record_path().exists()

    first = await _refused()

    expected = {
        "count": 1,
        "last_at": _NOW.isoformat(),
        "last_dir": str(repo / ".axon" / "pending"),
        "last_reason": first.reason,
    }
    assert json.loads(_record_path().read_text(encoding="utf-8")) == expected

    second = await _refused()

    assert json.loads(_record_path().read_text(encoding="utf-8")) == {
        **expected,
        "count": 2,
        "last_reason": second.reason,
    }
    assert "literal user prompt" not in _record_path().read_text(encoding="utf-8")


async def test_unwritable_record_does_not_change_the_refusal(tmp_path, monkeypatch, capsys):
    """Criterion 16: the record is best effort."""
    from axon.cli import pb
    from tests.cli.test_hook_spool import _StoreDown

    repo = _unignorable_repo(tmp_path, monkeypatch)
    _record_path().mkdir(parents=True)

    await _refused()

    lines = capsys.readouterr().err.splitlines()
    assert len([ln for ln in lines if ln.startswith("[axon] could not record spool refusal:")]) == 1

    monkeypatch.setattr("axon.store.session_store.SessionStore", _StoreDown)
    await pb._save_session_memory_with_spool(_memory())

    err = capsys.readouterr().err
    assert "[axon] could not record spool refusal:" in err
    assert "spool not ignored:" in err
    assert list(repo.rglob("*.json")) == []


async def test_failed_record_replace_leaves_no_temp_file(tmp_path, monkeypatch):
    _unignorable_repo(tmp_path, monkeypatch)

    def full_disk(*_a, **_kw):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("axon.store.pending.os.replace", full_disk)

    await _refused()

    assert list(_record_path().parent.glob("spool-refusals*")) == []


def _memory():
    from axon.store.session_store import SessionMemory

    return SessionMemory(project="axon", summary="literal user prompt", raw_turns=0)
