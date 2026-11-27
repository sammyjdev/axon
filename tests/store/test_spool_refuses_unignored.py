"""The spool writes nothing git could stage (axon #202).

A payload may land only in a directory git provably ignores, or outside any
git work tree. Every repository here is a real temp repo; the suite's autouse
fixture isolates the global git config, so nothing is ignored by accident.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from axon.store import pending
from axon.store.pending import _pending_paths, write_pending
from tests.cli.test_spool_not_committable import _git

_PAYLOAD = {"kind": "session_memory", "summary": "literal user prompt"}


def _repo(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("AXON_DATA_ROOT", str(tmp_path / ".axon"))
    _git(tmp_path, "init")
    return tmp_path


async def _write(commit_hash: str = "session") -> Path:
    return await write_pending(payload=_PAYLOAD, commit_hash=commit_hash, paths=_pending_paths())


def _read_only_replace(*_a, **_kw):
    raise OSError(30, "Read-only file system")


async def test_write_pending_refuses_when_ignore_write_fails(tmp_path, monkeypatch):
    """Criterion 2: the rule cannot be written and git does not ignore the spool."""
    repo = _repo(tmp_path, monkeypatch)
    monkeypatch.setattr("axon.store.pending.os.replace", _read_only_replace)

    with pytest.raises(pending.SpoolNotIgnoredError) as refused:
        await _write()

    assert "OSError: [Errno 30] Read-only file system" in str(refused.value)
    assert str(refused.value).startswith(f"spool not ignored: {repo / '.axon' / 'pending'}: ")
    assert refused.value.directory == repo / ".axon" / "pending"
    assert str(refused.value).endswith(refused.value.reason)
    assert list((repo / ".axon" / "pending").glob("*.json")) == []
    assert list((repo / ".axon").glob(".gitignore.*.tmp")) == []
