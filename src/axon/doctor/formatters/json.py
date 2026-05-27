"""JSON formatter for ``pb doctor --ci`` (dec-114).

Stable schema. ``version`` is incremented on any breaking change to the
shape so downstream parsers can pin.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from axon.doctor import CheckResult, CheckStatus

SCHEMA_VERSION = "1"


def format_results(results: list[CheckResult]) -> str:
    summary: dict[str, int] = {
        CheckStatus.OK.value: 0,
        CheckStatus.WARN.value: 0,
        CheckStatus.FAIL.value: 0,
    }
    payload_checks: list[dict[str, str]] = []
    for r in results:
        summary[r.status.value] += 1
        payload_checks.append(
            {
                "name": r.name,
                "status": r.status.value,
                "detail": r.detail,
                "suggestion": r.suggestion,
            }
        )
    payload = {
        "version": SCHEMA_VERSION,
        "ts": datetime.now(UTC).isoformat(),
        "checks": payload_checks,
        "summary": summary,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
