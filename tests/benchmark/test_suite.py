from __future__ import annotations

import pytest

from prometheus.benchmark.suite import build_default_benchmark_cases, run_default_benchmarks


def test_default_benchmark_suite_has_three_fixed_cases() -> None:
    cases = build_default_benchmark_cases()

    assert len(cases) == 3
    assert tuple(case.suite for case in cases) == ("retrieval", "compression", "setup")


@pytest.mark.asyncio
async def test_default_benchmark_suite_runs_all_cases() -> None:
    summary = await run_default_benchmarks()

    assert summary.total == 3
    assert summary.passed == 3
