from __future__ import annotations

from axon.benchmark.contracts import (
    BenchmarkCheck,
    BenchmarkResult,
    BenchmarkRunSummary,
)
from axon.benchmark.reporting import (
    compare_benchmark_runs,
    format_benchmark_comparison,
)


def test_benchmark_reporting_flags_regressions_and_renders_delta() -> None:
    baseline = BenchmarkRunSummary(
        results=(
            BenchmarkResult(
                suite="compression",
                name="fallback",
                duration_ms=10.0,
                checks=(
                    BenchmarkCheck(
                        name="fallback_to_original",
                        passed=True,
                        expected="yes",
                        actual="yes",
                    ),
                ),
            ),
        )
    )
    current = BenchmarkRunSummary(
        results=(
            BenchmarkResult(
                suite="compression",
                name="fallback",
                duration_ms=12.0,
                checks=(
                    BenchmarkCheck(
                        name="fallback_to_original",
                        passed=False,
                        expected="yes",
                        actual="no",
                    ),
                ),
            ),
        )
    )

    report = compare_benchmark_runs(current, baseline)
    text = format_benchmark_comparison(report)

    assert len(report.regressions) == 1
    assert report.regressions[0].key == "compression/fallback"
    assert "baseline: 1/1 passed score=1.00" in text
    assert "current: 0/1 passed score=0.00" in text
    assert "- compression/fallback: 1.00 -> 0.00" in text
