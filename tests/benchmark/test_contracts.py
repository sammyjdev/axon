from __future__ import annotations

from axon.benchmark.contracts import BenchmarkCheck, BenchmarkResult


def test_benchmark_result_computes_score_and_failures() -> None:
    result = BenchmarkResult(
        suite="retrieval",
        name="first-fixture",
        duration_ms=12.5,
        checks=(
            BenchmarkCheck(name="strategy", passed=True, expected="balanced", actual="balanced"),
            BenchmarkCheck(name="segments", passed=False, expected="1", actual="0"),
        ),
    )

    assert result.success is False
    assert result.score == 0.5
    assert result.failure_count == 1
    assert result.failures == (
        BenchmarkCheck(name="segments", passed=False, expected="1", actual="0"),
    )
