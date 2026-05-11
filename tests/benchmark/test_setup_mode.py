from __future__ import annotations

import pytest

from prometheus.benchmark.harness import run_benchmarks
from prometheus.benchmark.setup_mode import (
    SETUP_MODE_SANITY_BENCHMARK,
    build_setup_mode_benchmark_case,
)


@pytest.mark.asyncio
async def test_setup_mode_sanity_benchmark_covers_supported_modes() -> None:
    summary = await run_benchmarks((build_setup_mode_benchmark_case(SETUP_MODE_SANITY_BENCHMARK),))

    assert summary.total == 1
    assert summary.passed == 1

    result = summary.results[0]
    assert result.suite == "setup"
    assert result.name == SETUP_MODE_SANITY_BENCHMARK.name
    assert result.success is True
    assert result.failure_count == 0
    assert any(check.name == "minimal.compose_profile" for check in result.checks)
    assert any(check.name == "remote-infra.validate_remote_services" for check in result.checks)
    assert any(check.name == "full-local.pull_models" for check in result.checks)
