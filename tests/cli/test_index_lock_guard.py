"""The shared _index_lock_guard serializes index operations machine-wide on
one lock root (data_root). When another indexer holds it, fatal=True exits the
CLI cleanly and fatal=False skips (so the watcher does not crash)."""

from __future__ import annotations

import types
from pathlib import Path

import pytest
import typer


async def test_guard_runs_when_lock_free(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from axon.cli import pb

    monkeypatch.setattr(pb, "_RUNTIME", types.SimpleNamespace(data_root=tmp_path))
    ran = False
    async with pb._index_lock_guard(fatal=True) as acquired:
        ran = acquired
    assert ran is True


async def test_guard_skips_when_held_non_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from axon.cli import pb
    from axon.store.index_lock import acquire_index_lock

    monkeypatch.setattr(pb, "_RUNTIME", types.SimpleNamespace(data_root=tmp_path))
    async with acquire_index_lock(tmp_path):
        async with pb._index_lock_guard(fatal=False, label="watch") as acquired:
            assert acquired is False


async def test_guard_exits_when_held_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from axon.cli import pb
    from axon.store.index_lock import acquire_index_lock

    monkeypatch.setattr(pb, "_RUNTIME", types.SimpleNamespace(data_root=tmp_path))
    async with acquire_index_lock(tmp_path):
        with pytest.raises(typer.Exit):
            async with pb._index_lock_guard(fatal=True):
                pass
