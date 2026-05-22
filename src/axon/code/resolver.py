"""Symbol resolution — derives graph edges from indexed source (T4.2).

Two edge kinds, both best-effort:

* ``calls`` — Symbol → Symbol, from the chunker's call extraction, kept only
  when the callee is a symbol defined somewhere in the repo (drops stdlib and
  third-party noise).
* ``imports`` — File → File, from Python ``import`` statements resolved against
  the repo's own module layout.

File nodes are referenced by their repo-relative path string.
"""

from __future__ import annotations

import ast
from pathlib import Path

from axon.code.indexer import _INDEXED_LANGUAGES, _iter_repo_files
from axon.core.edge import Edge
from axon.embedder.chunker import Chunk, chunk_source
from axon.embedder.graph_extractor import build_dependency_records
from axon.store.session_store import SessionStore


def _python_import_modules(source: str) -> set[str]:
    """Module names referenced by ``import``/``from`` statements."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _module_name(rel: Path) -> str:
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_module(mod: str, module_of: dict[Path, str]) -> Path | None:
    """Best-effort: a repo file whose dotted module equals or ends with ``mod``."""
    exact = [f for f, m in module_of.items() if m == mod]
    if exact:
        return min(exact, key=str)
    suffix = [f for f, m in module_of.items() if m.endswith(f".{mod}")]
    if suffix:
        return min(suffix, key=lambda f: len(module_of[f]))
    return None


def _call_edges(chunks: list[Chunk]) -> list[Edge]:
    known = {c.symbol for c in chunks}
    edges: list[Edge] = []
    for record in build_dependency_records(chunks):
        for callee in record.calls:
            if callee in known and callee != record.symbol:
                edges.append(
                    Edge(source_id=record.symbol, target_id=callee, type="calls")
                )
    return edges


def _import_edges(root: Path, sources: dict[Path, str]) -> list[Edge]:
    py_files = [f for f in sources if f.suffix == ".py"]
    module_of = {f: _module_name(f.relative_to(root)) for f in py_files}
    edges: list[Edge] = []
    for file_path in py_files:
        rel = str(file_path.relative_to(root))
        for mod in _python_import_modules(sources[file_path]):
            target = _resolve_module(mod, module_of)
            if target is not None and target != file_path:
                edges.append(
                    Edge(
                        source_id=rel,
                        target_id=str(target.relative_to(root)),
                        type="imports",
                    )
                )
    return edges


async def index_edges(repo_path: Path | str, *, store: SessionStore) -> list[Edge]:
    """Resolve and persist ``calls`` and ``imports`` edges for a repo."""
    root = Path(repo_path)
    sources: dict[Path, str] = {}
    chunks: list[Chunk] = []
    for file_path in _iter_repo_files(root):
        source = file_path.read_text(encoding="utf-8", errors="replace")
        sources[file_path] = source
        language = _INDEXED_LANGUAGES[file_path.suffix]
        chunks.extend(chunk_source(source, language, str(file_path)))

    edges = _call_edges(chunks) + _import_edges(root, sources)
    for edge in edges:
        await store.add_edge(edge)
    return edges
