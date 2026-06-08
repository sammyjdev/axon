"""Benchmark package.

Public names are exported lazily (PEP 562) so importing a single lightweight
benchmark — e.g. ``axon.benchmark.supersession`` — does not eagerly pull heavy
optional dependencies (``litellm``, ``mcp``) used only by other suites. The
import surface is unchanged: ``from axon.benchmark import FIRST_RETRIEVAL_BENCHMARK``
still works and loads its module on first access.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

# Exported name -> defining submodule. Resolved on first attribute access.
_EXPORTS = {
    "COMPRESSION_FALLBACK_BENCHMARK": "compression_fallback",
    "build_compression_fallback_benchmark_case": "compression_fallback",
    "BenchmarkCheck": "contracts",
    "BenchmarkResult": "contracts",
    "BenchmarkRunSummary": "contracts",
    "BenchmarkCase": "harness",
    "run_benchmarks": "harness",
    "BenchmarkComparisonEntry": "reporting",
    "BenchmarkComparisonReport": "reporting",
    "compare_benchmark_runs": "reporting",
    "format_benchmark_comparison": "reporting",
    "FIRST_RETRIEVAL_BENCHMARK": "retrieval",
    "build_retrieval_benchmark_case": "retrieval",
    "SETUP_MODE_SANITY_BENCHMARK": "setup_mode",
    "build_setup_mode_benchmark_case": "setup_mode",
    "build_default_benchmark_cases": "suite",
    "run_default_benchmarks": "suite",
}


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(f".{module}", __name__), name)


def __dir__() -> list[str]:
    return sorted(_EXPORTS)


if TYPE_CHECKING:  # eager names for static checkers / IDEs only
    from .compression_fallback import (
        COMPRESSION_FALLBACK_BENCHMARK,
        build_compression_fallback_benchmark_case,
    )
    from .contracts import BenchmarkCheck, BenchmarkResult, BenchmarkRunSummary
    from .harness import BenchmarkCase, run_benchmarks
    from .reporting import (
        BenchmarkComparisonEntry,
        BenchmarkComparisonReport,
        compare_benchmark_runs,
        format_benchmark_comparison,
    )
    from .retrieval import FIRST_RETRIEVAL_BENCHMARK, build_retrieval_benchmark_case
    from .setup_mode import SETUP_MODE_SANITY_BENCHMARK, build_setup_mode_benchmark_case
    from .suite import build_default_benchmark_cases, run_default_benchmarks


__all__ = [
    "COMPRESSION_FALLBACK_BENCHMARK",
    "BenchmarkCase",
    "BenchmarkCheck",
    "BenchmarkComparisonEntry",
    "BenchmarkComparisonReport",
    "BenchmarkResult",
    "BenchmarkRunSummary",
    "FIRST_RETRIEVAL_BENCHMARK",
    "SETUP_MODE_SANITY_BENCHMARK",
    "build_compression_fallback_benchmark_case",
    "build_default_benchmark_cases",
    "build_retrieval_benchmark_case",
    "build_setup_mode_benchmark_case",
    "compare_benchmark_runs",
    "format_benchmark_comparison",
    "run_default_benchmarks",
    "run_benchmarks",
]
