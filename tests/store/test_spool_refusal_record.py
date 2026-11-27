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
