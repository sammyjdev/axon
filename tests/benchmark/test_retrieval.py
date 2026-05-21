from __future__ import annotations

import pytest

from axon.benchmark.harness import run_benchmarks
from axon.benchmark.retrieval import (
    FIRST_RETRIEVAL_BENCHMARK,
    build_retrieval_benchmark_case,
)


@pytest.mark.asyncio
async def test_first_retrieval_benchmark_fixture_passes_with_local_fakes() -> None:
    summary = await run_benchmarks((build_retrieval_benchmark_case(FIRST_RETRIEVAL_BENCHMARK),))

    assert summary.total == 1
    assert summary.passed == 1

    result = summary.results[0]
    assert result.suite == "retrieval"
    assert result.name == FIRST_RETRIEVAL_BENCHMARK.name
    assert result.success is True
    assert result.score == 1.0

    check_names = {check.name for check in result.checks}
    assert check_names == {
        "task_type",
        "strategy",
        "segments",
        "top_symbol",
        "context_pack",
        "graph_appendix",
    }
