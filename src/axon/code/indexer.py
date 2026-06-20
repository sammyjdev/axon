"""Code indexer — builds Symbol nodes from a repo's source (T4.1).

Thin wrapper over the chunker: walks a repo (respecting ``.gitignore`` via
``git ls-files``), chunks every supported source file, and persists one
``symbol`` node per chunk into the SQLite graph.

Symbol ids are the bare symbol names produced by the chunker — the same
namespace the graph extractor (T4.2) emits for ``calls`` edges, so nodes and
edges line up. Cross-file resolution is best-effort by design.
"""

from __future__ import annotations

from pathlib import Path

from axon.core.symbol import Language, Symbol, SymbolType
from axon.embedder.chunker import ChunkType, chunk_source
from axon.store.session_store import SessionStore

_INDEXED_LANGUAGES: dict[str, Language] = {".py": "python", ".java": "java"}

# The chunker's ChunkType is finer-grained than Symbol's SymbolType.
_CHUNK_TYPE_TO_SYMBOL: dict[ChunkType, SymbolType] = {
    "method": "method",
    "constructor": "method",
    "function": "function",
    "class": "class",
    "interface": "interface",
    "enum": "enum",
    "annotation": "interface",
    "record": "class",
}


def _symbols_for_file(path: Path) -> list[Symbol]:
    language = _INDEXED_LANGUAGES.get(path.suffix)
    if language is None:
        return []
    source = path.read_text(encoding="utf-8", errors="replace")
    symbols: list[Symbol] = []
    for chunk in chunk_source(source, language, str(path)):
        symbols.append(
            Symbol(
                id=chunk.symbol,
                type=_CHUNK_TYPE_TO_SYMBOL[chunk.chunk_type],
                file=path,
                start_line=chunk.start_line,
                # an empty-file fallback chunk can report end_line < start_line
                end_line=max(chunk.end_line, chunk.start_line),
                language=language,
            )
        )
    return symbols


async def index_file(path: Path | str, *, store: SessionStore) -> list[Symbol]:
    """Index one source file: persist a node per symbol, return the symbols."""
    symbols = _symbols_for_file(Path(path))
    for symbol in symbols:
        await store.add_node(
            symbol.id,
            "symbol",
            label=symbol.id,
            payload=symbol.model_dump(mode="json"),
        )
    return symbols


def _iter_repo_files(root: Path) -> list[Path]:
    """List indexable files under root, respecting .gitignore via iter_git_files (D3)."""
    from axon.repo.file_walk import iter_git_files
    return iter_git_files(root, suffixes=set(_INDEXED_LANGUAGES.keys()))


async def index_repo(repo_path: Path | str, *, store: SessionStore) -> list[Symbol]:
    """Index every supported source file under ``repo_path``."""
    symbols: list[Symbol] = []
    for file_path in _iter_repo_files(Path(repo_path)):
        symbols.extend(await index_file(file_path, store=store))
    return symbols
