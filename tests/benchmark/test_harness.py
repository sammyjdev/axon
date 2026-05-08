from __future__ import annotations

import pytest

from prometheus.benchmark.contracts import BenchmarkCheck, BenchmarkResult
from prometheus.benchmark.harness import BenchmarkCase, run_benchmarks


@pytest.mark.asyncio
async def test_run_benchmarks_aggregates_results() -> None:
    async def passing() -> BenchmarkResult:
        return BenchmarkResult(
            suite="retrieval",
            name="passing",
            duration_ms=1.0,
            checks=(BenchmarkCheck(name="ok", passed=True, expected="yes", actual="yes"),),
        )

    async def failing() -> BenchmarkResult:
        return BenchmarkResult(
            suite="retrieval",
            name="failing",
            duration_ms=2.0,
            checks=(BenchmarkCheck(name="ok", passed=False, expected="yes", actual="no"),),
        )

    summary = await run_benchmarks(
        (
            BenchmarkCase(suite="retrieval", name="passing", run=passing),
            BenchmarkCase(suite="retrieval", name="failing", run=failing),
        )
    )

    assert summary.total == 2
    assert summary.passed == 1
    assert summary.failed == 1
    assert summary.score == 0.5
    assert tuple(result.name for result in summary.results) == ("passing", "failing")
