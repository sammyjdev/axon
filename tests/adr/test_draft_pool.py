"""Tests for axon.adr.draft_pool (dec-111)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from axon.adr.draft_pool import (
    DraftRecord,
    auto_dormancy_sweep,
    find_stale,
    list_drafts,
    mark_dormant,
    read_draft,
    write_draft,
)


def _record(commit_hash: str = "abc", **kw) -> DraftRecord:  # noqa: ANN003
    base = dict(
        commit_hash=commit_hash,
        title="Adopt repository pattern",
        context="Coupling between handler and store",
        decision="Introduce SessionRepository abstraction",
        rationale="Decouples persistence from HTTP",
        failed_layer="density",
        failed_reason="no_architectural_lexicon_outside_diff",
        structural_mode=False,
    )
    base.update(kw)
    return DraftRecord(**base)  # type: ignore[arg-type]


class TestWriteRead:
    def test_round_trip(self, tmp_path: Path) -> None:
        record = _record(commit_hash="cafebabe")
        path = write_draft(record, draft_dir=tmp_path)
        assert path.exists()
        assert path.name == "cafebabe.md"
        loaded = read_draft(path)
        assert loaded.commit_hash == "cafebabe"
        assert loaded.title == "Adopt repository pattern"
        assert loaded.decision == "Introduce SessionRepository abstraction"

    def test_special_characters_in_title(self, tmp_path: Path) -> None:
        record = _record(title='Title with "quotes" and\nnewlines')
        path = write_draft(record, draft_dir=tmp_path)
        loaded = read_draft(path)
        assert loaded.title == 'Title with "quotes" and\nnewlines'

    def test_dormant_flag_round_trips(self, tmp_path: Path) -> None:
        record = _record(commit_hash="x")
        record.dormant = True
        write_draft(record, draft_dir=tmp_path)
        loaded = read_draft(tmp_path / "x.md")
        assert loaded.dormant is True


class TestListDrafts:
    def test_excludes_dormant_by_default(self, tmp_path: Path) -> None:
        live = _record(commit_hash="live")
        dormant = _record(commit_hash="dormant")
        dormant.dormant = True
        write_draft(live, draft_dir=tmp_path)
        write_draft(dormant, draft_dir=tmp_path)

        drafts = list_drafts(draft_dir=tmp_path)
        assert len(drafts) == 1
        assert drafts[0].commit_hash == "live"

    def test_include_dormant(self, tmp_path: Path) -> None:
        live = _record(commit_hash="live")
        dormant = _record(commit_hash="dormant")
        dormant.dormant = True
        write_draft(live, draft_dir=tmp_path)
        write_draft(dormant, draft_dir=tmp_path)

        all_drafts = list_drafts(draft_dir=tmp_path, include_dormant=True)
        assert len(all_drafts) == 2

    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        assert list_drafts(draft_dir=tmp_path / "missing") == []


class TestMarkDormant:
    def test_mark_dormant_returns_true_and_persists(self, tmp_path: Path) -> None:
        write_draft(_record(commit_hash="x"), draft_dir=tmp_path)
        assert mark_dormant("x", draft_dir=tmp_path) is True
        loaded = read_draft(tmp_path / "x.md")
        assert loaded.dormant is True

    def test_mark_dormant_missing_returns_false(self, tmp_path: Path) -> None:
        assert mark_dormant("nope", draft_dir=tmp_path) is False


class TestFindStale:
    def test_draft_with_no_l1_full_is_stale(self, tmp_path: Path) -> None:
        record = _record(commit_hash="abc")
        # created 25 hours ago, never L1-full validated
        record.created_at = datetime.now(UTC) - timedelta(hours=25)
        write_draft(record, draft_dir=tmp_path)
        stale = find_stale(draft_dir=tmp_path, ttl_hours=24)
        assert len(stale) == 1
        assert stale[0].commit_hash == "abc"

    def test_recently_validated_not_stale(self, tmp_path: Path) -> None:
        record = _record(commit_hash="fresh")
        record.last_l1_full_at = datetime.now(UTC)
        write_draft(record, draft_dir=tmp_path)
        stale = find_stale(draft_dir=tmp_path, ttl_hours=24)
        assert stale == []


class TestAutoDormancySweep:
    def test_old_drafts_become_dormant(self, tmp_path: Path) -> None:
        old = _record(commit_hash="old")
        old.created_at = datetime.now(UTC) - timedelta(days=31)
        new = _record(commit_hash="new")
        write_draft(old, draft_dir=tmp_path)
        write_draft(new, draft_dir=tmp_path)

        transitioned = auto_dormancy_sweep(draft_dir=tmp_path, dormancy_days=30)
        assert "old" in transitioned
        assert "new" not in transitioned
        assert read_draft(tmp_path / "old.md").dormant is True
        assert read_draft(tmp_path / "new.md").dormant is False
