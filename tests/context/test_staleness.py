from __future__ import annotations

from datetime import UTC, datetime

from axon.context.staleness import (
    StaleReplacement,
    StalenessAssessment,
    assess_staleness,
    detect_stale_replacements,
)


def test_assess_staleness_marks_old_records_as_stale() -> None:
    assessment = assess_staleness(
        {"modified_at": "2025-01-01T00:00:00+00:00"},
        now=datetime(2026, 5, 8, tzinfo=UTC),
    )

    assert assessment == StalenessAssessment(
        score=1.0,
        is_stale=True,
        reasons=("age_exceeds_stale_window",),
        replacement_family=None,
    )


def test_assess_staleness_marks_deprecated_records_as_stale() -> None:
    assessment = assess_staleness(
        {"status": "deprecated", "path": "notes/runbook.md"},
        now=datetime(2026, 5, 8, tzinfo=UTC),
    )

    assert assessment == StalenessAssessment(
        score=1.0,
        is_stale=True,
        reasons=("explicitly_deprecated",),
        replacement_family="notes/runbook.md",
    )


def test_detect_stale_replacements_prefers_newest_record_in_family() -> None:
    replacements = detect_stale_replacements(
        [
            {
                "id": "old",
                "path": "runbooks/search.md",
                "modified_at": "2025-01-01T00:00:00+00:00",
            },
            {
                "id": "new",
                "path": "runbooks/search.md",
                "modified_at": "2026-04-01T00:00:00+00:00",
            },
            {
                "id": "other",
                "path": "runbooks/other.md",
                "modified_at": "2026-04-01T00:00:00+00:00",
            },
        ],
        now=datetime(2026, 5, 8, tzinfo=UTC),
    )

    assert replacements == [
        StaleReplacement(
            stale_id="old",
            replacement_id="new",
            reason="newer_record_in_family",
        )
    ]
