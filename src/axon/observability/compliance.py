from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class ComplianceEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision_id: str
    reason_code: str
    policy_version: str
    route: str
    model: str | None = None
    caller: str | None = None
    ctx: str | None = None
    allowed: bool = False
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


def emit_compliance_event(event: ComplianceEvent) -> None:
    logger.info("compliance_event=%s", json.dumps(event.model_dump(), ensure_ascii=True))
