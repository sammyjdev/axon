from __future__ import annotations

from dataclasses import dataclass

from prometheus.benchmark.contracts import (
    BenchmarkCheck,
    BenchmarkResult,
    BenchmarkRunSummary,
)


@dataclass(frozen=True)
class BenchmarkComparisonEntry:
    suite: str
    name: str
    baseline_score: float
    current_score: float
    baseline_success: bool
    current_success: bool

    @property
    def key(self) -> str:
        return f"{self.suite}/{self.name}"

    @property
    def delta(self) -> float:
        return self.current_score - self.baseline_score

    @property
    def is_regression(self) -> bool:
        return self.baseline_success and not self.current_success

    @property
    def is_improvement(self) -> bool:
        return self.current_score > self.baseline_score


@dataclass(frozen=True)
class BenchmarkComparisonReport:
    baseline: BenchmarkRunSummary
    current: BenchmarkRunSummary
    entries: tuple[BenchmarkComparisonEntry, ...]

    @property
    def regressions(self) -> tuple[BenchmarkComparisonEntry, ...]:
        return tuple(entry for entry in self.entries if entry.is_regression)

    @property
    def improvements(self) -> tuple[BenchmarkComparisonEntry, ...]:
        return tuple(entry for entry in self.entries if entry.is_improvement)


def compare_benchmark_runs(
    current: BenchmarkRunSummary,
    baseline: BenchmarkRunSummary,
) -> BenchmarkComparisonReport:
    baseline_index = {_result_key(result): result for result in baseline.results}
    current_index = {_result_key(result): result for result in current.results}
    keys = tuple(sorted(set(baseline_index) | set(current_index)))

    entries: list[BenchmarkComparisonEntry] = []
    for key in keys:
        baseline_result = baseline_index.get(key)
        current_result = current_index.get(key)
        if baseline_result is None:
            baseline_result = _missing_result(key)
        if current_result is None:
            current_result = _missing_result(key)
        entries.append(
            BenchmarkComparisonEntry(
                suite=current_result.suite,
                name=current_result.name,
                baseline_score=baseline_result.score,
                current_score=current_result.score,
                baseline_success=baseline_result.success,
                current_success=current_result.success,
            )
        )

    return BenchmarkComparisonReport(
        baseline=baseline,
        current=current,
        entries=tuple(entries),
    )


def format_benchmark_comparison(report: BenchmarkComparisonReport) -> str:
    lines = [
        (
            f"baseline: {report.baseline.passed}/{report.baseline.total} "
            f"passed score={report.baseline.score:.2f}"
        ),
        (
            f"current: {report.current.passed}/{report.current.total} "
            f"passed score={report.current.score:.2f}"
        ),
    ]

    if report.regressions:
        lines.append("regressions:")
        for entry in report.regressions:
            lines.append(
                f"- {entry.key}: {entry.baseline_score:.2f} -> "
                f"{entry.current_score:.2f}"
            )
    else:
        lines.append("regressions: none")

    if report.improvements:
        lines.append("improvements:")
        for entry in report.improvements:
            lines.append(
                f"- {entry.key}: {entry.baseline_score:.2f} -> "
                f"{entry.current_score:.2f}"
            )

    return "\n".join(lines)


def _result_key(result: BenchmarkResult) -> str:
    return f"{result.suite}/{result.name}"


def _missing_result(key: str) -> BenchmarkResult:
    suite, name = key.split("/", 1)
    return BenchmarkResult(
        suite=suite,
        name=name,
        duration_ms=0.0,
        checks=(
            BenchmarkCheck(name="missing", passed=False, expected="present", actual="missing"),
        ),
    )
