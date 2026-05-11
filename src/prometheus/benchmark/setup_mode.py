from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter

from prometheus.benchmark.contracts import BenchmarkCheck, BenchmarkResult
from prometheus.benchmark.harness import BenchmarkCase
from prometheus.config.platform import PlatformConfig, build_setup_plan


@dataclass(frozen=True)
class SetupModeBenchmarkCase:
    runtime_mode: str
    platform_config: PlatformConfig
    remote_infra_host: str | None
    expected_compose_profile: str | None
    expected_start_local_stack: bool
    expected_validate_remote_services: bool
    expected_pull_models: tuple[str, ...]


@dataclass(frozen=True)
class SetupModeBenchmarkFixture:
    name: str
    cases: tuple[SetupModeBenchmarkCase, ...]
    metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)


MAC_SETUP_CONFIG = PlatformConfig(
    platform="mac",
    embedding_providers=["CoreMLExecutionProvider", "CPUExecutionProvider"],
    ollama_flash=False,
    max_models=1,
    model_primary="gemma4:e4b",
    model_knowledge="gemma4:e4b",
    keep_alive="10m",
)

GPU_SETUP_CONFIG = PlatformConfig(
    platform="pc",
    embedding_providers=["CUDAExecutionProvider"],
    ollama_flash=True,
    max_models=2,
    model_primary="gemma4:e4b",
    model_knowledge="gemma4:26b",
    keep_alive="-1",
)


SETUP_MODE_SANITY_BENCHMARK = SetupModeBenchmarkFixture(
    name="setup-mode-sanity",
    cases=(
        SetupModeBenchmarkCase(
            runtime_mode="minimal",
            platform_config=MAC_SETUP_CONFIG,
            remote_infra_host=None,
            expected_compose_profile=None,
            expected_start_local_stack=False,
            expected_validate_remote_services=False,
            expected_pull_models=(),
        ),
        SetupModeBenchmarkCase(
            runtime_mode="remote-infra",
            platform_config=GPU_SETUP_CONFIG,
            remote_infra_host="desktop.local",
            expected_compose_profile=None,
            expected_start_local_stack=False,
            expected_validate_remote_services=True,
            expected_pull_models=(),
        ),
        SetupModeBenchmarkCase(
            runtime_mode="hybrid-local",
            platform_config=MAC_SETUP_CONFIG,
            remote_infra_host=None,
            expected_compose_profile="cpu",
            expected_start_local_stack=True,
            expected_validate_remote_services=False,
            expected_pull_models=("phi3:mini", "gemma4:e4b"),
        ),
        SetupModeBenchmarkCase(
            runtime_mode="full-local",
            platform_config=GPU_SETUP_CONFIG,
            remote_infra_host=None,
            expected_compose_profile="gpu",
            expected_start_local_stack=True,
            expected_validate_remote_services=False,
            expected_pull_models=("phi3:mini", "gemma4:e4b", "gemma4:26b"),
        ),
    ),
    metadata=(("kind", "setup"), ("deterministic", "true")),
)


def build_setup_mode_benchmark_case(fixture: SetupModeBenchmarkFixture) -> BenchmarkCase:
    return BenchmarkCase(
        suite="setup",
        name=fixture.name,
        run=lambda: execute_setup_mode_benchmark(fixture),
    )


async def execute_setup_mode_benchmark(
    fixture: SetupModeBenchmarkFixture,
) -> BenchmarkResult:
    started = perf_counter()
    checks: list[BenchmarkCheck] = []

    for case in fixture.cases:
        plan = build_setup_plan(
            runtime_mode=case.runtime_mode,  # type: ignore[arg-type]
            platform_config=case.platform_config,
            remote_infra_host=case.remote_infra_host,
        )
        actual_models = plan.pull_models
        checks.extend(
            (
                BenchmarkCheck(
                    name=f"{case.runtime_mode}.compose_profile",
                    passed=plan.compose_profile == case.expected_compose_profile,
                    expected=case.expected_compose_profile or "-",
                    actual=plan.compose_profile or "-",
                ),
                BenchmarkCheck(
                    name=f"{case.runtime_mode}.start_local_stack",
                    passed=plan.start_local_stack == case.expected_start_local_stack,
                    expected=str(case.expected_start_local_stack).lower(),
                    actual=str(plan.start_local_stack).lower(),
                ),
                BenchmarkCheck(
                    name=f"{case.runtime_mode}.validate_remote_services",
                    passed=plan.validate_remote_services == case.expected_validate_remote_services,
                    expected=str(case.expected_validate_remote_services).lower(),
                    actual=str(plan.validate_remote_services).lower(),
                ),
                BenchmarkCheck(
                    name=f"{case.runtime_mode}.pull_models",
                    passed=actual_models == case.expected_pull_models,
                    expected=",".join(case.expected_pull_models) or "-",
                    actual=",".join(actual_models) or "-",
                ),
            )
        )

    duration_ms = (perf_counter() - started) * 1000
    return BenchmarkResult(
        suite="setup",
        name=fixture.name,
        duration_ms=duration_ms,
        checks=tuple(checks),
        metadata=fixture.metadata,
    )
