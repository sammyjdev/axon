from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import tree_sitter_java as tsjava
import tree_sitter_python as tspython
import tree_sitter_typescript as tsts
from pydantic import BaseModel, Field
from tree_sitter import Language, Node, Parser

ChunkType = Literal[
    "method", "constructor", "function", "class", "interface",
    "enum", "annotation", "record", "section"
]

_JAVA_LANGUAGE = Language(tsjava.language())
_PARSER = Parser(_JAVA_LANGUAGE)

_PY_LANGUAGE = Language(tspython.language())
_PY_PARSER = Parser(_PY_LANGUAGE)

_TS_LANGUAGE = Language(tsts.language_typescript())
_TS_PARSER = Parser(_TS_LANGUAGE)
_TSX_LANGUAGE = Language(tsts.language_tsx())
_TSX_PARSER = Parser(_TSX_LANGUAGE)

# node types que geram chunks
_METHOD_TYPES = {"method_declaration", "constructor_declaration"}
_CLASS_TYPES = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "record",
    "annotation_type_declaration": "annotation",
}
_MAX_CHUNK_LINES = 80


class Chunk(BaseModel):
    symbol: str
    chunk_type: ChunkType
    start_line: int  # 1-based
    end_line: int  # 1-based inclusive
    content: str
    file_path: str
    language: str = "java"
    metadata: dict = Field(default_factory=dict)


def _get_identifier(node: Node) -> str:
    for child in node.children:
        if child.type == "identifier":
            return child.text.decode()
    return "unknown"


def _extract_chunks(
    node: Node,
    source: bytes,
    file_path: str,
    parent_name: str = "",
    anon_counter: list[int] | None = None,
) -> list[Chunk]:
    if anon_counter is None:
        anon_counter = [0]

    chunks: list[Chunk] = []

    for child in node.children:
        if child.type in _CLASS_TYPES:
            chunk_type = _CLASS_TYPES[child.type]
            name = _get_identifier(child)
            # Símbolo usa apenas o nome simples (sem prefixo da classe pai)
            full_name = name
            start = child.start_point[0] + 1  # 1-based
            end = child.end_point[0] + 1

            # Records, enums, annotations e interfaces internas são sempre chunk único
            if chunk_type == "interface" and parent_name:
                chunks.append(
                    Chunk(
                        symbol=full_name,
                        chunk_type=chunk_type,
                        start_line=start,
                        end_line=end,
                        content=source[child.start_byte : child.end_byte].decode(errors="replace"),
                        file_path=file_path,
                    )
                )
                continue

            # Classes internas (inner classes) são chunk único
            if chunk_type == "class" and parent_name:
                chunks.append(
                    Chunk(
                        symbol=full_name,
                        chunk_type=chunk_type,
                        start_line=start,
                        end_line=end,
                        content=source[child.start_byte : child.end_byte].decode(errors="replace"),
                        file_path=file_path,
                    )
                )
                continue

            if chunk_type in ("record", "enum", "annotation"):
                lines_count = end - start + 1
                if lines_count > _MAX_CHUNK_LINES:
                    chunks.extend(
                        _split_large_node(child, source, full_name, chunk_type, file_path)
                    )
                else:
                    chunks.append(
                        Chunk(
                            symbol=full_name,
                            chunk_type=chunk_type,
                            start_line=start,
                            end_line=end,
                            content=source[child.start_byte : child.end_byte].decode(
                                errors="replace"
                            ),
                            file_path=file_path,
                        )
                    )
                continue

            # Recursivamente extrai métodos internos
            inner = _extract_chunks(child, source, file_path, full_name, anon_counter)

            if inner:
                # Se há métodos internos, expõe os métodos diretamente
                method_chunks = [c for c in inner if c.chunk_type in ("method", "constructor")]
                if method_chunks:
                    chunks.extend(inner)
                else:
                    # Só sub-classes, sem métodos: expõe sub-classes
                    chunks.extend(inner)
            else:
                # Classe sem métodos: chunk completo, fragmentado se muito grande
                lines_count = end - start + 1
                if lines_count > _MAX_CHUNK_LINES:
                    chunks.extend(
                        _split_large_node(child, source, full_name, chunk_type, file_path)
                    )
                else:
                    chunks.append(
                        Chunk(
                            symbol=full_name,
                            chunk_type=chunk_type,
                            start_line=start,
                            end_line=end,
                            content=source[child.start_byte : child.end_byte].decode(
                                errors="replace"
                            ),
                            file_path=file_path,
                        )
                    )

        elif child.type in _METHOD_TYPES:
            name = _get_identifier(child)
            # Símbolo é apenas o nome do método (sem prefixo da classe)
            full_name = name
            start = child.start_point[0] + 1
            end = child.end_point[0] + 1
            method_chunk_type: ChunkType = (
                "constructor" if child.type == "constructor_declaration" else "method"
            )
            lines = end - start + 1
            if lines > _MAX_CHUNK_LINES:
                chunks.extend(
                    _split_large_node(child, source, full_name, method_chunk_type, file_path)
                )
            else:
                chunks.append(
                    Chunk(
                        symbol=full_name,
                        chunk_type=method_chunk_type,
                        start_line=start,
                        end_line=end,
                        content=source[child.start_byte : child.end_byte].decode(errors="replace"),
                        file_path=file_path,
                    )
                )

        elif child.type == "object_creation_expression":
            # Anonymous class dentro de expressão
            for sub in child.children:
                if sub.type == "class_body":
                    anon_counter[0] += 1
                    anon_name = f"{parent_name}$anon{anon_counter[0]}"
                    start = child.start_point[0] + 1
                    end = child.end_point[0] + 1
                    inner = _extract_chunks(sub, source, file_path, anon_name, anon_counter)
                    if inner:
                        chunks.extend(inner)
                    else:
                        chunks.append(
                            Chunk(
                                symbol=anon_name,
                                chunk_type="class",
                                start_line=start,
                                end_line=end,
                                content=source[child.start_byte : child.end_byte].decode(
                                    errors="replace"
                                ),
                                file_path=file_path,
                            )
                        )
        else:
            # Desce recursivamente em outros nós (block, field_declaration, etc.)
            chunks.extend(_extract_chunks(child, source, file_path, parent_name, anon_counter))

    return chunks


def _split_large_node(
    node: Node,
    source: bytes,
    symbol: str,
    chunk_type: ChunkType,
    file_path: str,
    language: str = "java",
) -> list[Chunk]:
    """Divide nó que excede _MAX_CHUNK_LINES em sub-chunks de linhas.

    ``language`` defaults to "java" (the original caller) and MUST be passed
    explicitly for other languages so sub-chunks are not mis-tagged - the
    Chunk model defaults language to "java".
    """
    content = source[node.start_byte : node.end_byte].decode(errors="replace")
    lines = content.splitlines()
    start_line = node.start_point[0] + 1
    chunks: list[Chunk] = []
    for i in range(0, len(lines), _MAX_CHUNK_LINES):
        part_lines = lines[i : i + _MAX_CHUNK_LINES]
        chunks.append(
            Chunk(
                symbol=f"{symbol}[{i // _MAX_CHUNK_LINES}]",
                chunk_type=chunk_type,
                start_line=start_line + i,
                end_line=start_line + i + len(part_lines) - 1,
                content="\n".join(part_lines),
                file_path=file_path,
                language=language,
            )
        )
    return chunks


def _split_lines_into_chunks(
    lines: list[str],
    start_line_1based: int,
    symbol: str,
    chunk_type: ChunkType,
    file_path: str,
    language: str,
) -> list[Chunk]:
    """Divide a list of text lines into sub-chunks of _MAX_CHUNK_LINES each.

    Used for Markdown sections and plain-text files that have no tree-sitter
    parse tree. Distinct from _split_large_node, which operates on tree-sitter
    Node byte ranges. All sub-chunks (including index 0) are named symbol[idx].
    """
    result: list[Chunk] = []
    for i in range(0, max(len(lines), 1), _MAX_CHUNK_LINES):
        part = lines[i : i + _MAX_CHUNK_LINES]
        idx = i // _MAX_CHUNK_LINES
        result.append(
            Chunk(
                symbol=f"{symbol}[{idx}]",
                chunk_type=chunk_type,
                start_line=start_line_1based + i,
                end_line=start_line_1based + i + len(part) - 1,
                content="\n".join(part),
                file_path=file_path,
                language=language,
            )
        )
    return result


def _chunk_markdown(source: str, file_path: str) -> list[Chunk]:
    """Chunk a Markdown file by heading boundaries.

    Each heading (# through ######) starts a new section. Content before the
    first heading becomes a chunk with symbol = Path(file_path).stem.
    Sections exceeding _MAX_CHUNK_LINES are split via _split_lines_into_chunks.
    A file with no headings is treated as a single section and split on line cap.
    """
    lines = source.splitlines()
    _HEADING_RE = re.compile(r"^#{1,6}\s+(.+)")

    sections: list[tuple[str, int, list[str]]] = []  # (symbol, start_1based, lines)
    current_symbol = Path(file_path).stem
    current_start = 1
    current_lines: list[str] = []

    for lineno, line in enumerate(lines, start=1):
        m = _HEADING_RE.match(line)
        if m:
            if current_lines:
                sections.append((current_symbol, current_start, current_lines))
            current_symbol = re.sub(r"[^a-zA-Z0-9_]", "_", m.group(1).strip())[:64]
            current_start = lineno
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_lines:
        sections.append((current_symbol, current_start, current_lines))

    if not sections:
        return [
            Chunk(
                symbol=Path(file_path).stem,
                chunk_type="section",
                start_line=1,
                end_line=1,
                content="",
                file_path=file_path,
                language="markdown",
            )
        ]

    chunks: list[Chunk] = []
    for symbol, start_1based, sec_lines in sections:
        if len(sec_lines) > _MAX_CHUNK_LINES:
            chunks.extend(
                _split_lines_into_chunks(
                    sec_lines, start_1based, symbol, "section", file_path, "markdown"
                )
            )
        else:
            chunks.append(
                Chunk(
                    symbol=symbol,
                    chunk_type="section",
                    start_line=start_1based,
                    end_line=start_1based + len(sec_lines) - 1,
                    content="\n".join(sec_lines),
                    file_path=file_path,
                    language="markdown",
                )
            )
    return chunks


def chunk_java_file(path: str | Path) -> list[Chunk]:
    """
    Parseia um arquivo Java via tree-sitter e retorna lista de Chunks.
    Cada método, construtor, classe, interface, enum, record e annotation
    vira um chunk com metadados de localização.
    """
    path = Path(path)
    source = path.read_bytes()
    tree = _PARSER.parse(source)
    chunks = _extract_chunks(tree.root_node, source, str(path))

    # Fallback: arquivo sem nenhum símbolo reconhecível → chunk único
    if not chunks:
        lines = source.decode(errors="replace").splitlines()
        chunks.append(
            Chunk(
                symbol=path.stem,
                chunk_type="class",
                start_line=1,
                end_line=len(lines),
                content=source.decode(errors="replace"),
                file_path=str(path),
            )
        )

    return chunks


# ---------------------------------------------------------------------------
# Python chunker (via tree-sitter-python)
# ---------------------------------------------------------------------------
#
# Tree-sitter replaced the stdlib ``ast`` path for two reasons surfaced by
# dogfood: (1) ``ast.parse`` raises on partial/broken files, collapsing
# everything to a "1 chunk = file" fallback and losing all symbols for
# mid-edit captures; (2) ``ast`` is version-locked to the interpreter
# running AXON, so Python 3.12+ syntax (PEP 695 ``type X = ...`` aliases,
# generic function parameters) crashed when AXON ran on an older Python.
# Tree-sitter's grammar covers modern syntax and recovers from errors
# instead of failing closed.


def _chunk_python(source: str, file_path: str) -> list[Chunk]:
    """Parse Python source with tree-sitter and emit per-symbol chunks.

    Yields chunks for every ``function_definition`` (including async
    ``async def``) found anywhere in the tree. Functions defined inside
    a ``class_definition`` are tagged ``method``; the rest are
    ``function``. Tree-sitter's error recovery means a partially broken
    file still yields chunks for the surrounding well-formed code.

    Falls back to "1 chunk = file" only if the tree-sitter parse itself
    raises (defensive — has not been observed in practice).
    """
    lines = source.splitlines()
    try:
        tree = _PY_PARSER.parse(source.encode("utf-8"))
    except Exception:
        return [_python_fallback_chunk(source, lines, file_path)]

    chunks: list[Chunk] = []
    _walk_python(tree.root_node, source, lines, file_path, in_class=False, chunks=chunks, tree=tree)
    if not chunks:
        fb = _python_fallback_chunk(source, lines, file_path)
        fb.metadata["_tree"] = tree
        chunks.append(fb)
    return chunks


def _walk_python(
    node: Node,
    source: str,
    lines: list[str],
    file_path: str,
    *,
    in_class: bool,
    chunks: list[Chunk],
    tree: object | None = None,
) -> None:
    """Recurse the tree, emitting a chunk for each function definition.

    ``in_class`` controls whether the next ``function_definition`` is
    tagged ``method`` (inside a class body) or ``function`` (anywhere
    else, including nested inner functions).
    """
    if node.type in ("function_definition",):
        symbol = _python_node_identifier(node)
        _sym = symbol or Path(file_path).stem
        _chunk_type: ChunkType = "method" if in_class else "function"
        _start = node.start_point[0] + 1
        _end = node.end_point[0] + 1
        if (_end - _start) > _MAX_CHUNK_LINES:
            sub_chunks = _split_large_node(
                node,
                source.encode("utf-8") if isinstance(source, str) else source,
                _sym,
                _chunk_type,
                file_path,
                language="python",
            )
            if tree is not None:
                for sc in sub_chunks:
                    sc.metadata["_tree"] = tree
            chunks.extend(sub_chunks)
        else:
            chunks.append(
                Chunk(
                    symbol=_sym,
                    chunk_type=_chunk_type,
                    start_line=_start,
                    end_line=_end,
                    content="\n".join(lines[node.start_point[0] : node.end_point[0] + 1]),
                    file_path=file_path,
                    language="python",
                    metadata={"_tree": tree} if tree is not None else {},
                )
            )
        # Recurse to catch inner functions (still tagged as functions
        # unless inside another class).
        for child in node.children:
            _walk_python(
                child, source, lines, file_path,
                in_class=False, chunks=chunks, tree=tree,
            )
        return

    if node.type == "class_definition":
        for child in node.children:
            _walk_python(
                child, source, lines, file_path,
                in_class=True, chunks=chunks, tree=tree,
            )
        return

    for child in node.children:
        _walk_python(
            child, source, lines, file_path,
            in_class=in_class, chunks=chunks, tree=tree,
        )


def _python_node_identifier(node: Node) -> str:
    """Return the symbol name of a function/class definition node."""
    for child in node.children:
        if child.type == "identifier":
            return child.text.decode("utf-8", errors="replace")
    return "unknown"


def _python_fallback_chunk(source: str, lines: list[str], file_path: str) -> Chunk:
    return Chunk(
        symbol=Path(file_path).stem,
        chunk_type="class",
        start_line=1,
        end_line=len(lines) or 1,
        content=source,
        file_path=file_path,
        language="python",
    )


# ---------------------------------------------------------------------------
# TypeScript chunker (regex-based)
# ---------------------------------------------------------------------------

_TS_METHOD_RE = re.compile(  # pragma: no cover
    r"^[ \t]*(?:(?:public|private|protected|static|async|override|abstract)\s+)*"
    r"([a-zA-Z_$][a-zA-Z0-9_$]*)\s*[<(]",
    re.MULTILINE,
)
_TS_FUNCTION_RE = re.compile(  # pragma: no cover
    r"^(?:export\s+)?(?:async\s+)?function\s+([a-zA-Z_$][a-zA-Z0-9_$]*)\s*[<(]",
    re.MULTILINE,
)
_TS_CLASS_RE = re.compile(  # pragma: no cover
    r"^(?:export\s+)?(?:abstract\s+)?class\s+([a-zA-Z_$][a-zA-Z0-9_$]*)",
    re.MULTILINE,
)
_SKIP_TS_NAMES = {  # pragma: no cover
    "constructor",
    "if",
    "for",
    "while",
    "switch",
    "catch",
    "return",
    "new",
    "typeof",
    "instanceof",
    "delete",
    "void",
    "throw",
}


def _chunk_typescript(source: str, file_path: str) -> list[Chunk]:
    """Parse TypeScript / TSX via tree-sitter-typescript.

    Replaces the previous regex-based parser. Tree-sitter recognises:
    function declarations (including generic signatures), class methods,
    arrow functions assigned to const/let/property, get/set accessors,
    decorators, and JSX/TSX. Error recovery yields chunks for
    well-formed parts of partially broken files.
    """
    parser = _TSX_PARSER if file_path.endswith(".tsx") else _TS_PARSER
    lines = source.splitlines()
    try:
        tree = parser.parse(source.encode("utf-8"))
    except Exception:
        return [_ts_fallback_chunk(source, lines, file_path)]

    chunks: list[Chunk] = []
    _walk_ts(tree.root_node, lines, file_path, in_class=False, chunks=chunks, tree=tree)
    if not chunks:
        fb = _ts_fallback_chunk(source, lines, file_path)
        fb.metadata["_tree"] = tree
        chunks.append(fb)
    return chunks


def _walk_ts(
    node: Node,
    lines: list[str],
    file_path: str,
    *,
    in_class: bool,
    chunks: list[Chunk],
    tree: object | None = None,
) -> None:
    if node.type in ("function_declaration", "method_definition"):
        name = _ts_identifier(node) or "anonymous"
        chunks.extend(
            _ts_chunk_from_node(node, lines, file_path, name, in_class, tree=tree)
        )
        # Recurse to catch nested functions
        for child in node.children:
            _walk_ts(child, lines, file_path, in_class=False, chunks=chunks, tree=tree)
        return

    if node.type in ("class_declaration", "class_body"):
        for child in node.children:
            _walk_ts(child, lines, file_path, in_class=True, chunks=chunks, tree=tree)
        return

    # Arrow functions / function expressions bound to a name
    if node.type in (
        "variable_declarator",
        "public_field_definition",
        "property_signature",
    ):
        name_node = node.child_by_field_name("name")
        value_node = node.child_by_field_name("value")
        if name_node and value_node and value_node.type in (
            "arrow_function",
            "function_expression",
        ):
            name = name_node.text.decode("utf-8", errors="replace")
            chunks.extend(
                _ts_chunk_from_node(node, lines, file_path, name, in_class, tree=tree)
            )
            return

    for child in node.children:
        _walk_ts(child, lines, file_path, in_class=in_class, chunks=chunks, tree=tree)


def _ts_identifier(node: Node) -> str | None:
    name = node.child_by_field_name("name")
    if name is not None:
        return name.text.decode("utf-8", errors="replace")
    for child in node.children:
        if child.type in ("identifier", "property_identifier"):
            return child.text.decode("utf-8", errors="replace")
    return None


def _ts_chunk_from_node(
    node: Node,
    lines: list[str],
    file_path: str,
    name: str,
    in_class: bool,
    *,
    tree: object | None = None,
) -> list[Chunk]:
    """Return one or more Chunks for this node, splitting if it exceeds _MAX_CHUNK_LINES."""
    start = node.start_point[0]
    end = node.end_point[0]
    _chunk_type: ChunkType = "method" if in_class else "function"
    if (end - start + 1) > _MAX_CHUNK_LINES:
        sub_chunks = _split_lines_into_chunks(
            lines[start : end + 1],
            start + 1,
            name,
            _chunk_type,
            file_path,
            "typescript",
        )
        if tree is not None:
            for sc in sub_chunks:
                sc.metadata["_tree"] = tree
        return sub_chunks
    return [
        Chunk(
            symbol=name,
            chunk_type=_chunk_type,
            start_line=start + 1,
            end_line=end + 1,
            content="\n".join(lines[start : end + 1]),
            file_path=file_path,
            language="typescript",
            metadata={"_tree": tree} if tree is not None else {},
        )
    ]


def _ts_fallback_chunk(source: str, lines: list[str], file_path: str) -> Chunk:
    return Chunk(
        symbol=Path(file_path).stem,
        chunk_type="class",
        start_line=1,
        end_line=len(lines) or 1,
        content=source,
        file_path=file_path,
        language="typescript",
    )


def _chunk_typescript_legacy(source: str, file_path: str) -> list[Chunk]:  # pragma: no cover
    """Original regex-based parser. Retained only for reference; not on the path."""
    lines = source.splitlines()
    chunks: list[Chunk] = []

    # Top-level exported functions
    for m in _TS_FUNCTION_RE.finditer(source):
        name = m.group(1)
        if name in _SKIP_TS_NAMES:
            continue
        start_line = source[: m.start()].count("\n") + 1
        end_line = _find_block_end(lines, start_line - 1)
        content = "\n".join(lines[start_line - 1 : end_line])
        chunks.append(
            Chunk(
                symbol=name,
                chunk_type="method",
                start_line=start_line,
                end_line=end_line,
                content=content,
                file_path=file_path,
                language="typescript",
            )
        )

    # Class methods
    for m in _TS_METHOD_RE.finditer(source):
        name = m.group(1)
        if name in _SKIP_TS_NAMES or name[0].isupper():
            continue
        line_text = source[: m.start()].count("\n")
        # Only inside a class (indented)
        indent = len(m.group(0)) - len(m.group(0).lstrip())
        if indent < 2:
            continue
        start_line = line_text + 1
        end_line = _find_block_end(lines, start_line - 1)
        content = "\n".join(lines[start_line - 1 : end_line])
        # Avoid duplicates
        if any(c.symbol == name and c.start_line == start_line for c in chunks):
            continue
        chunks.append(
            Chunk(
                symbol=name,
                chunk_type="method",
                start_line=start_line,
                end_line=end_line,
                content=content,
                file_path=file_path,
                language="typescript",
            )
        )

    if not chunks:
        chunks.append(
            Chunk(
                symbol=Path(file_path).stem,
                chunk_type="class",
                start_line=1,
                end_line=len(lines),
                content=source,
                file_path=file_path,
                language="typescript",
            )
        )

    return chunks


def _find_block_end(lines: list[str], start_idx: int) -> int:  # pragma: no cover
    """Find closing brace for a block starting at start_idx (0-based)."""
    depth = 0
    for i in range(start_idx, len(lines)):
        depth += lines[i].count("{") - lines[i].count("}")
        if depth > 0 and i > start_idx:
            continue
        if depth <= 0 and i > start_idx:
            return i + 1
    return len(lines)


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------


def chunk_source(source: str, language: str, file_path: str) -> list[Chunk]:
    """
    Parse source text for the given language and return a list of Chunks.
    Supported: java, python, typescript.
    """
    if language == "java":
        source_bytes = source.encode()
        tree = _PARSER.parse(source_bytes)
        chunks = _extract_chunks(tree.root_node, source_bytes, file_path)
        if not chunks:
            lines = source.splitlines()
            chunks.append(
                Chunk(
                    symbol=Path(file_path).stem,
                    chunk_type="class",
                    start_line=1,
                    end_line=len(lines),
                    content=source,
                    file_path=file_path,
                )
            )
        for _c in chunks:
            _c.metadata["_tree"] = tree
        return chunks
    elif language == "python":
        return _chunk_python(source, file_path)
    elif language in ("typescript", "ts"):
        return _chunk_typescript(source, file_path)
    elif language == "markdown":
        return _chunk_markdown(source, file_path)
    else:
        lines = source.splitlines()
        stem = Path(file_path).stem
        if len(lines) > _MAX_CHUNK_LINES:
            return _split_lines_into_chunks(lines, 1, stem, "section", file_path, language)
        return [
            Chunk(
                symbol=stem,
                chunk_type="section",
                start_line=1,
                end_line=len(lines) or 1,
                content=source,
                file_path=file_path,
                language=language,
            )
        ]
