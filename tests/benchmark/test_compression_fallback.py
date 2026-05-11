from __future__ import annotations

import pytest

from prometheus.benchmark.compression_fallback import (
    COMPRESSION_FALLBACK_BENCHMARK,
    build_compression_fallback_benchmark_case,
)
from prometheus.benchmark.harness import run_benchmarks


@pytest.mark.asyncio
async def test_compression_fallback_benchmark_returns_to_original_context() -> None:
    summary = await run_benchmarks(
        (build_compression_fallback_benchmark_case(COMPRESSION_FALLBACK_BENCHMARK),)
    )

    assert summary.total == 1
    assert summary.passed == 1

    result = summary.results[0]
    assert result.suite == "compression"
    assert result.name == COMPRESSION_FALLBACK_BENCHMARK.name
    assert result.success is True
    assert result.score == 1.0
    assert {check.name for check in result.checks} == {
        "fallback_to_original",
        "fallback_note",
    }
