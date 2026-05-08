from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from prometheus.benchmark.contracts import BenchmarkCheck, BenchmarkResult
from prometheus.benchmark.harness import BenchmarkCase
from prometheus.mcp import server
from prometheus.router.classifier import TaskType


@dataclass(frozen=True)
class RetrievalExpectation:
    task_type: str
    strategy: str
    segments: int
    top_symbol: str
    required_substrings: tuple[str, ...]


@dataclass(frozen=True)
class RetrievalBenchmarkFixture:
    name: str
    query: str
    ctx: str | None
    language: str | None
    max_depth: int
    max_nodes: int
    max_tokens: int
    hits: tuple[dict[str, Any], ...]
    graph_nodes: tuple[str, ...]
    expectation: RetrievalExpectation
    metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)


class _FakeVectorStore:
    def __init__(self, results: tuple[dict[str, Any], ...]) -> None:
        self._results = list(results)

    async def search(self, **_kwargs: object) -> list[dict[str, Any]]:
        return list(self._results)


class _FakeGraphStore:
    def __init__(self, nodes: tuple[str, ...]) -> None:
        self._nodes = list(nodes)

    async def connect(self) -> None:
        return None

    async def traverse(self, symbol: str, max_depth: int, max_nodes: int) -> dict[str, object]:
        return {
            "root": symbol,
            "nodes": self._nodes[:max_nodes],
            "max_depth": max_depth,
        }


FIRST_RETRIEVAL_BENCHMARK = RetrievalBenchmarkFixture(
    name="code-analysis-context-pack",
    query="upsert vector",
    ctx="knowledge",
    language=None,
    max_depth=2,
    max_nodes=25,
    max_tokens=1200,
    hits=(
        {
            "score": 0.91,
            "payload": {
                "symbol": "upsert",
                "language": "python",
                "file_path": "/tmp/vector_store.py",
                "content": "async def upsert(self, chunk): ...",
            },
        },
    ),
    graph_nodes=("VectorStore", "QdrantClient"),
    expectation=RetrievalExpectation(
        task_type="CODE_ANALYSIS",
        strategy="balanced",
        segments=1,
        top_symbol="upsert",
        required_substrings=(
            "## Context pack",
            "### upsert (python)",
            "Root: upsert",
            "Nodes: VectorStore, QdrantClient",
        ),
    ),
    metadata=(("kind", "retrieval"), ("deterministic", "true")),
)


def build_retrieval_benchmark_case(fixture: RetrievalBenchmarkFixture) -> BenchmarkCase:
    return BenchmarkCase(
        suite="retrieval",
        name=fixture.name,
        run=lambda: execute_retrieval_benchmark(fixture),
    )


async def execute_retrieval_benchmark(fixture: RetrievalBenchmarkFixture) -> BenchmarkResult:
    started = perf_counter()

    with (
        patch.object(server, "_get_vector_store", return_value=_FakeVectorStore(fixture.hits)),
        patch.object(server, "_get_embedder", return_value=SimpleNamespace(embed_one=lambda _query: [0.1])),
        patch.object(server, "_get_graph_store", return_value=_FakeGraphStore(fixture.graph_nodes)),
        patch(
            "prometheus.router.classifier.classify_task_with_source",
            return_value=(TaskType.CODE_ANALYSIS, "benchmark"),
        ),
    ):
        response, pack, hits = await server._retrieve_context(
            query=fixture.query,
            ctx=fixture.ctx,
            language=fixture.language,
            max_depth=fixture.max_depth,
            max_nodes=fixture.max_nodes,
            max_tokens=fixture.max_tokens,
        )

    duration_ms = (perf_counter() - started) * 1000
    top_symbol = ((hits[0].get("payload") or {}).get("symbol", "")) if hits else ""
    checks = [
        BenchmarkCheck(
            name="task_type",
            passed=pack.task_type == fixture.expectation.task_type,
            expected=fixture.expectation.task_type,
            actual=pack.task_type,
        ),
        BenchmarkCheck(
            name="strategy",
            passed=pack.strategy.name == fixture.expectation.strategy,
            expected=fixture.expectation.strategy,
            actual=pack.strategy.name,
        ),
        BenchmarkCheck(
            name="segments",
            passed=len(pack.segments) == fixture.expectation.segments,
            expected=str(fixture.expectation.segments),
            actual=str(len(pack.segments)),
        ),
        BenchmarkCheck(
            name="top_symbol",
            passed=top_symbol == fixture.expectation.top_symbol,
            expected=fixture.expectation.top_symbol,
            actual=top_symbol,
        ),
        BenchmarkCheck(
            name="context_pack",
            passed="## Context pack" in response,
            expected="present",
            actual="present" if "## Context pack" in response else "missing",
        ),
        BenchmarkCheck(
            name="graph_appendix",
            passed="## Dependencias relacionadas (2-step)" in response,
            expected="present",
            actual="present" if "## Dependencias relacionadas (2-step)" in response else "missing",
        ),
    ]

    for required in fixture.expectation.required_substrings:
        if required not in response:
            checks.append(
                BenchmarkCheck(
                    name=f"contains:{required}",
                    passed=False,
                    expected="present",
                    actual="missing",
                )
            )

    return BenchmarkResult(
        suite="retrieval",
        name=fixture.name,
        duration_ms=duration_ms,
        checks=tuple(checks),
        metadata=fixture.metadata,
    )
