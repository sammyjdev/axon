from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BenchmarkCheck:
    name: str
    passed: bool
    expected: str
    actual: str


@dataclass(frozen=True)
class BenchmarkResult:
    suite: str
    name: str
    duration_ms: float
    checks: tuple[BenchmarkCheck, ...]
    metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def success(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def score(self) -> float:
        if not self.checks:
            return 0.0
        passed = sum(1 for check in self.checks if check.passed)
        return passed / len(self.checks)

    @property
    def failure_count(self) -> int:
        return sum(1 for check in self.checks if not check.passed)

    @property
    def failures(self) -> tuple[BenchmarkCheck, ...]:
        return tuple(check for check in self.checks if not check.passed)


@dataclass(frozen=True)
class BenchmarkRunSummary:
    results: tuple[BenchmarkResult, ...]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for result in self.results if result.success)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def score(self) -> float:
        if not self.results:
            return 0.0
        return sum(result.score for result in self.results) / len(self.results)
