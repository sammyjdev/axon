"""Tests for axon.adr.lesson_pool — drafts from the `Lesson:` trailer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from axon.adr.lesson_pool import (
    TELL_PROMPT,
    LessonDraftRecord,
    auto_dormancy_sweep,
    list_drafts,
    mark_dormant,
    read_draft,
    write_draft,
)


def _record(commit_hash: str = "abc", **kw) -> LessonDraftRecord:  # noqa: ANN003
    base = dict(
        commit_hash=commit_hash,
        title="retries need jitter",
        context="src/client.py\nsrc/retry.py",
    )
    base.update(kw)
    return LessonDraftRecord(**base)  # type: ignore[arg-type]


class TestWriteRead:
    def test_round_trip(self, tmp_path: Path) -> None:
        record = _record(commit_hash="cafebabe")
        path = write_draft(record, draft_dir=tmp_path)
        assert path.exists()
        assert path.name == "cafebabe.md"
        loaded = read_draft(path)
        assert loaded.commit_hash == "cafebabe"
        assert loaded.title == "retries need jitter"
        assert loaded.context == "src/client.py\nsrc/retry.py"

    def test_tell_defaults_to_prompt_not_blank(self, tmp_path: Path) -> None:
        # A blank tell is worse than no draft - it must prompt for what
        # the author SAW while confused, not silently omit the field.
        record = _record(commit_hash="x")
        path = write_draft(record, draft_dir=tmp_path)
        assert TELL_PROMPT in path.read_text(encoding="utf-8")
        loaded = read_draft(path)
        assert loaded.tell == TELL_PROMPT

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
