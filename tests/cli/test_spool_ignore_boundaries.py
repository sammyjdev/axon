from __future__ import annotations

import pytest

from axon.doctor.checks.capture import check_pending_backlog
from axon.store.pending import _pending_paths, drain_pending, quarantine_invalid
from tests.cli.test_spool_not_committable import (
    _git,
    _spool_a_session_through_the_hook,
    _staged,
)


def test_spooling_preserves_an_existing_axon_gitignore(tmp_path, monkeypatch):
    repo = tmp_path
    monkeypatch.setenv("AXON_DATA_ROOT", str(repo / ".axon"))
    _git(repo, "init")

    axon_dir = repo / ".axon"
    axon_dir.mkdir()
    ignore = axon_dir / ".gitignore"
    ignore.write_text("# user marker\nlocal-only.txt\n", encoding="utf-8")
    (axon_dir / "local-only.txt").write_text("private", encoding="utf-8")

    _spool_a_session_through_the_hook(repo, monkeypatch)

    staged = _staged(repo)
    assert "# user marker" in ignore.read_text(encoding="utf-8")
    assert ".axon/local-only.txt" not in staged, "the user's nested ignore rule was lost"
    assert not [path for path in staged if path.startswith(".axon/pending/")]


def test_spool_ignore_rules_leave_adr_drafts_committable(tmp_path, monkeypatch):
    repo = tmp_path
    monkeypatch.setenv("AXON_DATA_ROOT", str(repo / ".axon"))
    _git(repo, "init")

    draft = repo / ".axon" / "adr-draft" / "decision.md"
    draft.parent.mkdir(parents=True)
    draft.write_text("tracked by design", encoding="utf-8")

    _spool_a_session_through_the_hook(repo, monkeypatch)

    staged = _staged(repo)
    assert ".axon/adr-draft/decision.md" in staged
    assert not [path for path in staged if path.startswith(".axon/pending/")]


@pytest.mark.asyncio
async def test_default_pending_layout_keeps_the_quarantine_log_at_the_data_root(
    tmp_path, monkeypatch
):
    data_root = tmp_path / ".axon"
    monkeypatch.setenv("AXON_DATA_ROOT", str(data_root))
    paths = _pending_paths()
    assert paths.pending_dir == data_root / "pending"
    assert paths.quarantine_dir == data_root / "pending-quarantine"
    assert paths.quarantine_log == data_root / "quarantine.jsonl"

    paths.pending_dir.mkdir(parents=True)
    malformed = paths.pending_dir / "bad.json"
    malformed.write_text("{not json", encoding="utf-8")
    destination = await quarantine_invalid(malformed, reason="invalid JSON", paths=paths)

    assert destination.parent == data_root / "pending-quarantine"
    assert paths.quarantine_log.is_file()


@pytest.mark.asyncio
async def test_non_json_pending_entries_are_not_drained_or_counted(tmp_path, monkeypatch):
    data_root = tmp_path / ".axon"
    monkeypatch.setenv("AXON_DATA_ROOT", str(data_root))
    paths = _pending_paths()
    paths.pending_dir.mkdir(parents=True)
    marker = paths.pending_dir / ".gitignore"
    marker.write_text("*\n", encoding="utf-8")

    async def sink(_payload: dict) -> None:
        raise AssertionError("non-JSON pending metadata reached the sink")

    result = await drain_pending(paths, sink=sink)

    assert result.processed == result.quarantined == result.retried == 0
    assert marker.is_file()
    assert check_pending_backlog(data_root=data_root).detail == "empty"
