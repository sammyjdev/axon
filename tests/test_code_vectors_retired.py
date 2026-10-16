"""Guard: code vectorization, ADR inference and the router are retired (2026-10-15).

AXON stopped embedding code: `search_code` drew about one call a day, three of four
of them went back to grep, and the vectors held 95% of the database and the only
provider key. ADR inference, the `ask` tool and the router went with it, so no
commit and no MCP tool calls an LLM. See docs/decisions/dec-138-stop-code-vectors.md.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

RETIRED_PACKAGES = (
    "benchmark",
    "code",
    "domains",
    "embedder",
    "expansion",
    "registry",
    "resilience",
    "retrieval",
    "router",
    "watcher",
)

RETIRED_MODULES = (
    "src/embedder",
    "src/axon/adr/audit.py",
    "src/axon/adr/commit_context.py",
    "src/axon/adr/draft_pool.py",
    "src/axon/adr/gates",
    "src/axon/adr/inference.py",
    "src/axon/adr/lexicon.py",
    "src/axon/context/contracts.py",
    "src/axon/context/graph_source.py",
    "src/axon/doctor/checks/embedder_live.py",
    "src/axon/doctor/checks/index_composition.py",
    "src/axon/doctor/checks/recall_savings.py",
    "src/axon/memory/session_compressor.py",
    "src/axon/obsidian/importer.py",
    "src/axon/store/pg_graph_repository.py",
    "src/axon/store/pg_symbol_deps.py",
    "src/axon/store/pg_vector_store.py",
    "src/axon/store/vector_common.py",
    "src/axon/validation/judge.py",
    "contrib/claude-code/axon-search-first.py",
)

RETIRED_DEPENDENCIES = (
    "defusedxml",
    "fastembed",
    "glyph-kg",
    "ollama",
    "pgvector",
    "pymupdf",
    "tree-sitter",
    "watchdog",
)

_IMPORT = re.compile(
    r"^\s*(?:from|import)\s+(?:axon\.(?:" + "|".join(RETIRED_PACKAGES) + r")\b"
    r"|fastembed\b|tree_sitter\b|glyph\b|pgvector\b)",
    re.M,
)


def _has_source(path: Path) -> bool:
    # A deleted package can leave an untracked __pycache__ behind; only source counts.
    return path.is_file() or any(path.rglob("*.py"))


def test_retired_packages_are_deleted() -> None:
    left = [p for p in RETIRED_PACKAGES if _has_source(REPO / "src" / "axon" / p)]
    assert not left, f"retired packages still in the tree: {left}"


def test_retired_modules_are_deleted() -> None:
    left = [rel for rel in RETIRED_MODULES if _has_source(REPO / rel)]
    assert not left, f"retired modules still in the tree: {left}"


def test_nothing_imports_a_retired_package() -> None:
    offenders = []
    for root in ("src", "scripts", "tests", "benchmarks"):
        for path in (REPO / root).rglob("*.py"):
            if path.resolve() == Path(__file__).resolve():
                continue
            if _IMPORT.search(path.read_text(encoding="utf-8")):
                offenders.append(path.relative_to(REPO).as_posix())
    assert not offenders, f"imports of retired code remain: {offenders}"


def test_retired_dependencies_are_not_declared() -> None:
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    declared = " ".join(
        [*project["dependencies"], *sum(project.get("optional-dependencies", {}).values(), [])]
    ).lower()
    left = [dep for dep in RETIRED_DEPENDENCIES if dep in declared]
    assert not left, f"retired dependencies still declared: {left}"
