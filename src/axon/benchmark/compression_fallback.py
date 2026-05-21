from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from unittest.mock import patch

from axon.benchmark.contracts import BenchmarkCheck, BenchmarkResult
from axon.benchmark.harness import BenchmarkCase
from axon.router import compressor


@dataclass(frozen=True)
class CompressionFallbackBenchmarkFixture:
    name: str
    source_text: str
    compressed_text: str
    max_tokens: int
    metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)


def _build_source_text() -> str:
    segment = (
        "[0.9100] /tmp/vector_store.py :: upsert :: async def upsert(self, chunk): ..."
    )
    filler = " ".join(["filler"] * 100)
    return "\n\n".join(
        (
            segment,
            "[0.8800] /tmp/vector_store.py :: search :: async def search(self, query): ...",
            filler,
        )
    )


COMPRESSION_FALLBACK_BENCHMARK = CompressionFallbackBenchmarkFixture(
    name="guarded-compression-falls-back-to-full-context",
    source_text=_build_source_text(),
    compressed_text="summary only",
    max_tokens=64,
    metadata=(("kind", "compression"), ("deterministic", "true")),
)


def build_compression_fallback_benchmark_case(
    fixture: CompressionFallbackBenchmarkFixture,
) -> BenchmarkCase:
    return BenchmarkCase(
        suite="compression",
        name=fixture.name,
        run=lambda: execute_compression_fallback_benchmark(fixture),
    )


async def execute_compression_fallback_benchmark(
    fixture: CompressionFallbackBenchmarkFixture,
) -> BenchmarkResult:
    started = perf_counter()

    async def _fake_compress(*_args: object, **_kwargs: object) -> tuple[str, str | None]:
        return fixture.compressed_text, None

    with patch.object(compressor, "caveman_compress", side_effect=_fake_compress):
        compressed_text, note = await compressor.caveman_compress_guarded(
            fixture.source_text,
            max_tokens=fixture.max_tokens,
        )

    duration_ms = (perf_counter() - started) * 1000
    checks = (
        BenchmarkCheck(
            name="fallback_to_original",
            passed=compressed_text == fixture.source_text,
            expected="original context",
            actual=(
                "original context"
                if compressed_text == fixture.source_text
                else "compressed context"
            ),
        ),
        BenchmarkCheck(
            name="fallback_note",
            passed=bool(note) and "missing source symbol(s)" in note,
            expected="missing source symbol(s)",
            actual=note or "missing",
        ),
    )
    return BenchmarkResult(
        suite="compression",
        name=fixture.name,
        duration_ms=duration_ms,
        checks=checks,
        metadata=fixture.metadata,
    )
