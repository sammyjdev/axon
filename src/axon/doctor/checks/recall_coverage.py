from __future__ import annotations

from pathlib import Path

from axon.config.runtime import load_runtime_config
from axon.doctor import CheckResult, CheckStatus
from axon.observability.coverage import aggregate_coverage
from axon.observability.savings import aggregate_recall_savings

# Below this, the per-call savings ratio describes a tool that is barely being
# reached for, and reporting it alone would imply savings that never happened.
_LOW_COVERAGE = 0.10


def check_recall_coverage(*, data_root: Path | None = None) -> CheckResult:
    resolved_root = data_root or load_runtime_config().data_root
    recall = resolved_root / "recall"

    coverage = aggregate_coverage(recall / "opportunities.jsonl", recall / "chunks.jsonl")
    if coverage.opportunities == 0:
        return CheckResult(
            name="recall.coverage",
            status=CheckStatus.OK,
            detail="skipped: no coverage telemetry yet",
        )

    ratio = coverage.coverage_ratio or 0.0
    savings = aggregate_recall_savings(recall / "chunks.jsonl")
    per_call = savings.savings_ratio
    delivered = coverage.delivered_savings(per_call) if per_call is not None else None

    detail = (
        f"coverage={ratio * 100:.1f}% "
        f"(searches={coverage.searches} vs code-reads={coverage.opportunities}) "
        f"delivered={'n/a' if delivered is None else f'{delivered * 100:.1f}%'} "
        f"reads_without_prior_search={coverage.reads_without_prior_search} "
        f"reads_after_search={coverage.reads_after_search}"
    )
    status = CheckStatus.WARN if ratio < _LOW_COVERAGE else CheckStatus.OK
    return CheckResult(name="recall.coverage", status=status, detail=detail)
