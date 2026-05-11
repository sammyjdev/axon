from __future__ import annotations

from prometheus.benchmark.compression_fallback import (
    COMPRESSION_FALLBACK_BENCHMARK,
    build_compression_fallback_benchmark_case,
)
from prometheus.benchmark.contracts import BenchmarkRunSummary
from prometheus.benchmark.harness import BenchmarkCase, run_benchmarks
from prometheus.benchmark.retrieval import (
    FIRST_RETRIEVAL_BENCHMARK,
    build_retrieval_benchmark_case,
)
from prometheus.benchmark.setup_mode import (
    SETUP_MODE_SANITY_BENCHMARK,
    build_setup_mode_benchmark_case,
)


def build_default_benchmark_cases() -> tuple[BenchmarkCase, ...]:
    return (
        build_retrieval_benchmark_case(FIRST_RETRIEVAL_BENCHMARK),
        build_compression_fallback_benchmark_case(COMPRESSION_FALLBACK_BENCHMARK),
        build_setup_mode_benchmark_case(SETUP_MODE_SANITY_BENCHMARK),
    )


async def run_default_benchmarks() -> BenchmarkRunSummary:
    return await run_benchmarks(build_default_benchmark_cases())
