from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, UTC

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ComplianceEvent:
    decision_id: str
    reason_code: str
    policy_version: str
    route: str
    model: str | None = None
    caller: str | None = None
    ctx: str | None = None
    allowed: bool = False
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


def emit_compliance_event(event: ComplianceEvent) -> None:
    logger.info("compliance_event=%s", json.dumps(asdict(event), ensure_ascii=True))
