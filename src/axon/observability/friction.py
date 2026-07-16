from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from axon.observability.trace_store import TraceRecord


@dataclass(frozen=True)
class FrictionPattern:
    caller: str
    reason_code: str
    ctx: str | None
    count: int
    distinct_days: int
    last_ts: str


def aggregate_friction(
    records: Sequence[TraceRecord], *, min_days: int = 2
) -> list[FrictionPattern]:
    """Aggregate repeated policy denials by day.

    Distinct days are a session proxy because TraceRecord has no session_id.
    """
    groups: dict[tuple[str, str, str | None], list[TraceRecord]] = defaultdict(list)
    for record in records:
        if record.stage != "policy" or record.payload.get("allowed") is not False:
            continue
        reason_code = record.payload.get("reason_code")
        if not isinstance(reason_code, str):
            continue
        groups[(record.caller, reason_code, record.ctx)].append(record)

    patterns = [
        FrictionPattern(
            caller=caller,
            reason_code=reason_code,
            ctx=ctx,
            count=len(group),
            distinct_days=len({record.ts[:10] for record in group}),
            last_ts=max(record.ts for record in group),
        )
        for (caller, reason_code, ctx), group in groups.items()
        if len({record.ts[:10] for record in group}) >= min_days
    ]
    return sorted(
        patterns,
        key=lambda pattern: (pattern.distinct_days, pattern.count),
        reverse=True,
    )
