"""Tests for axon.adr.draft_dormancy — shared draft-pool aging rule.

Factored out of draft_pool.py so the Lesson pool (Lesson: trailer)
reuses the exact same 30-day-dormancy math instead of a copy-pasted
loop with its own off-by-one risk.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from axon.adr.draft_dormancy import DEFAULT_DORMANCY_DAYS, due_for_dormancy


@dataclass
class _Record:
    commit_hash: str
    created_at: datetime
    dormant: bool = False


class TestDueForDormancy:
    def test_old_record_is_due(self) -> None:
        old = _Record("old", created_at=datetime.now(UTC) - timedelta(days=31))
        due = due_for_dormancy([old], dormancy_days=30)
        assert due == [old]

    def test_fresh_record_is_not_due(self) -> None:
        fresh = _Record("fresh", created_at=datetime.now(UTC))
        assert due_for_dormancy([fresh], dormancy_days=30) == []

    def test_already_dormant_record_is_excluded(self) -> None:
        old = _Record(
            "old",
            created_at=datetime.now(UTC) - timedelta(days=31),
            dormant=True,
        )
        assert due_for_dormancy([old], dormancy_days=30) == []

    def test_default_dormancy_days_is_thirty(self) -> None:
        assert DEFAULT_DORMANCY_DAYS == 30

    def test_mixed_batch_returns_only_due(self) -> None:
        old = _Record("old", created_at=datetime.now(UTC) - timedelta(days=31))
        fresh = _Record("fresh", created_at=datetime.now(UTC))
        assert due_for_dormancy([old, fresh], dormancy_days=30) == [old]
