"""Human-readable formatter for ``pb doctor`` (dec-114)."""

from __future__ import annotations

from axon.doctor import CheckResult, CheckStatus

_STATUS_PREFIX = {
    CheckStatus.OK: "[ ok ]",
    CheckStatus.WARN: "[warn]",
    CheckStatus.FAIL: "[FAIL]",
}


def format_results(results: list[CheckResult]) -> str:
    """Return the table lines for stdout. One result per line."""
    lines: list[str] = []
    for r in results:
        prefix = _STATUS_PREFIX[r.status]
        line = f"{prefix} {r.name:<32} {r.detail}"
        lines.append(line)
        if r.status is not CheckStatus.OK and r.suggestion:
            lines.append(f"        → {r.suggestion}")
    return "\n".join(lines)
