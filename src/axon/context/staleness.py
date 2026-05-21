from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

_EXPLICITLY_STALE_STATUSES = {"deprecated", "obsolete", "superseded", "archived"}
_STALE_AFTER_DAYS = 180


@dataclass(frozen=True)
class StalenessAssessment:
    score: float
    is_stale: bool
    reasons: tuple[str, ...]
    replacement_family: str | None


@dataclass(frozen=True)
class StaleReplacement:
    stale_id: str
    replacement_id: str
    reason: str


def assess_staleness(
    metadata: Mapping[str, object],
    *,
    now: datetime | None = None,
    stale_after_days: int = _STALE_AFTER_DAYS,
) -> StalenessAssessment:
    normalized_now = now or datetime.now(UTC)
    replacement_family = _replacement_family(metadata)
    reasons: list[str] = []

    status = str(metadata.get("status", "")).strip().lower()
    if status in _EXPLICITLY_STALE_STATUSES:
        reasons.append("explicitly_deprecated")
        return StalenessAssessment(
            score=1.0,
            is_stale=True,
            reasons=tuple(reasons),
            replacement_family=replacement_family,
        )

    modified_at = _parse_timestamp(metadata.get("modified_at") or metadata.get("updated_at"))
    if modified_at is None:
        return StalenessAssessment(
            score=0.0,
            is_stale=False,
            reasons=(),
            replacement_family=replacement_family,
        )

    age_days = max(0, (normalized_now - modified_at).days)
    if age_days >= stale_after_days:
        reasons.append("age_exceeds_stale_window")
        return StalenessAssessment(
            score=1.0,
            is_stale=True,
            reasons=tuple(reasons),
            replacement_family=replacement_family,
        )

    return StalenessAssessment(
        score=0.0,
        is_stale=False,
        reasons=(),
        replacement_family=replacement_family,
    )


def detect_stale_replacements(
    records: Sequence[Mapping[str, object]],
    *,
    now: datetime | None = None,
    stale_after_days: int = _STALE_AFTER_DAYS,
) -> list[StaleReplacement]:
    latest_by_family: dict[str, Mapping[str, object]] = {}
    records_by_family: dict[str, list[Mapping[str, object]]] = defaultdict(list)

    for record in records:
        family = _replacement_family(record)
        if not family:
            continue
        records_by_family[family].append(record)
        current_latest = latest_by_family.get(family)
        if current_latest is None or _record_sort_key(record) > _record_sort_key(current_latest):
            latest_by_family[family] = record

    replacements: list[StaleReplacement] = []
    for family, family_records in records_by_family.items():
        replacement = latest_by_family[family]
        replacement_id = str(replacement.get("id", "")).strip()
        replacement_ts = _parse_timestamp(replacement.get("modified_at") or replacement.get("updated_at"))
        if not replacement_id or replacement_ts is None:
            continue

        for record in family_records:
            stale_id = str(record.get("id", "")).strip()
            if not stale_id or stale_id == replacement_id:
                continue

            assessment = assess_staleness(record, now=now, stale_after_days=stale_after_days)
            record_ts = _parse_timestamp(record.get("modified_at") or record.get("updated_at"))
            if not assessment.is_stale or record_ts is None or record_ts >= replacement_ts:
                continue

            replacements.append(
                StaleReplacement(
                    stale_id=stale_id,
                    replacement_id=replacement_id,
                    reason="newer_record_in_family",
                )
            )

    return replacements


def _replacement_family(metadata: Mapping[str, object]) -> str | None:
    for key in ("canonical_id", "path", "source_path", "title"):
        value = str(metadata.get(key, "")).strip()
        if value:
            return value
    return None


def _parse_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str):
        return None

    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _record_sort_key(record: Mapping[str, object]) -> tuple[datetime, str]:
    timestamp = _parse_timestamp(record.get("modified_at") or record.get("updated_at"))
    return timestamp or datetime.min.replace(tzinfo=UTC), str(record.get("id", ""))
