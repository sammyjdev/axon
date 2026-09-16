from dataclasses import dataclass

from axon.activity.models import ActivityEvent, ActivitySession, SourceCursor


@dataclass
class AdapterResult:
    events: list[ActivityEvent]
    sessions: list[ActivitySession]
    cursor: SourceCursor
    warnings: list[str]
