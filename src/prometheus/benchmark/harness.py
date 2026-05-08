from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from prometheus.benchmark.contracts import BenchmarkResult, BenchmarkRunSummary


@dataclass(frozen=True)
class BenchmarkCase:
    suite: str
    name: str
    run: Callable[[], Awaitable[BenchmarkResult]]


async def run_benchmarks(cases: Sequence[BenchmarkCase]) -> BenchmarkRunSummary:
    results: list[BenchmarkResult] = []
    for case in cases:
        result = await case.run()
        results.append(result)
    return BenchmarkRunSummary(results=tuple(results))
