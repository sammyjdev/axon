from __future__ import annotations

from pathlib import Path

from axon.config.runtime import load_runtime_config
from axon.doctor import CheckResult, CheckStatus
from axon.observability.savings import aggregate_recall_savings

# Keep pb doctor bounded as chunks telemetry grows; recent usage is the signal.
_MAX_LINES = 5000


def check_recall_savings(*, data_root: Path | None = None) -> CheckResult:
    resolved_root = data_root or load_runtime_config().data_root
    aggregate = aggregate_recall_savings(
        resolved_root / "recall" / "chunks.jsonl",
        max_lines=_MAX_LINES,
    )
    if aggregate.requests == 0:
        return CheckResult(
            name="recall.savings",
            status=CheckStatus.OK,
            detail="skipped: no telemetry yet",
        )

    ratio = aggregate.savings_ratio
    savings = "n/a" if ratio is None else f"{ratio * 100:.1f}%"
    return CheckResult(
        name="recall.savings",
        status=CheckStatus.OK,
        detail=(
            f"savings={savings} requests={aggregate.requests} "
            f"returned={aggregate.returned_tokens:,} "
            f"counterfactual={aggregate.counterfactual_tokens:,} "
            "(vs reading files in full)"
        ),
    )
