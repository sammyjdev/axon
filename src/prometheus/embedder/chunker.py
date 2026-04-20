from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import tree_sitter_java as tsjava
from tree_sitter import Language, Node, Parser

ChunkType = Literal["method", "class", "interface", "enum", "annotation", "record"]

_JAVA_LANGUAGE = Language(tsjava.language())
_PARSER = Parser(_JAVA_LANGUAGE)

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


@dataclass
class Chunk:
    symbol: str
    chunk_type: ChunkType
    start_line: int   # 1-based
    end_line: int     # 1-based inclusive
    content: str
    file_path: str
    language: str = "java"
    metadata: dict = field(default_factory=dict)


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
                chunks.append(Chunk(
                    symbol=full_name,
                    chunk_type=chunk_type,
                    start_line=start,
                    end_line=end,
                    content=source[child.start_byte:child.end_byte].decode(errors="replace"),
                    file_path=file_path,
                ))
                continue

            # Classes internas (inner classes) são chunk único
            if chunk_type == "class" and parent_name:
                chunks.append(Chunk(
                    symbol=full_name,
                    chunk_type=chunk_type,
                    start_line=start,
                    end_line=end,
                    content=source[child.start_byte:child.end_byte].decode(errors="replace"),
                    file_path=file_path,
                ))
                continue

            if chunk_type in ("record", "enum", "annotation"):
                lines_count = end - start + 1
                if lines_count > _MAX_CHUNK_LINES:
                    chunks.extend(_split_large_node(child, source, full_name, chunk_type, file_path))
                else:
                    chunks.append(Chunk(
                        symbol=full_name,
                        chunk_type=chunk_type,
                        start_line=start,
                        end_line=end,
                        content=source[child.start_byte:child.end_byte].decode(errors="replace"),
                        file_path=file_path,
                    ))
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
                    chunks.extend(_split_large_node(child, source, full_name, chunk_type, file_path))
                else:
                    chunks.append(Chunk(
                        symbol=full_name,
                        chunk_type=chunk_type,
                        start_line=start,
                        end_line=end,
                        content=source[child.start_byte:child.end_byte].decode(errors="replace"),
                        file_path=file_path,
                    ))

        elif child.type in _METHOD_TYPES:
            name = _get_identifier(child)
            # Símbolo é apenas o nome do método (sem prefixo da classe)
            full_name = name
            start = child.start_point[0] + 1
            end = child.end_point[0] + 1
            method_chunk_type: ChunkType = "constructor" if child.type == "constructor_declaration" else "method"
            lines = end - start + 1
            if lines > _MAX_CHUNK_LINES:
                chunks.extend(_split_large_node(child, source, full_name, method_chunk_type, file_path))
            else:
                chunks.append(Chunk(
                    symbol=full_name,
                    chunk_type=method_chunk_type,
                    start_line=start,
                    end_line=end,
                    content=source[child.start_byte:child.end_byte].decode(errors="replace"),
                    file_path=file_path,
                ))

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
                        chunks.append(Chunk(
                            symbol=anon_name,
                            chunk_type="class",
                            start_line=start,
                            end_line=end,
                            content=source[child.start_byte:child.end_byte].decode(errors="replace"),
                            file_path=file_path,
                        ))
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
) -> list[Chunk]:
    """Divide nó que excede _MAX_CHUNK_LINES em sub-chunks de linhas."""
    content = source[node.start_byte:node.end_byte].decode(errors="replace")
    lines = content.splitlines()
    start_line = node.start_point[0] + 1
    chunks: list[Chunk] = []
    for i in range(0, len(lines), _MAX_CHUNK_LINES):
        part_lines = lines[i : i + _MAX_CHUNK_LINES]
        chunks.append(Chunk(
            symbol=f"{symbol}[{i // _MAX_CHUNK_LINES}]",
            chunk_type=chunk_type,
            start_line=start_line + i,
            end_line=start_line + i + len(part_lines) - 1,
            content="\n".join(part_lines),
            file_path=file_path,
        ))
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
        chunks.append(Chunk(
            symbol=path.stem,
            chunk_type="class",
            start_line=1,
            end_line=len(lines),
            content=source.decode(errors="replace"),
            file_path=str(path),
        ))

    return chunks


# ---------------------------------------------------------------------------
# Python chunker (via ast module)
# ---------------------------------------------------------------------------

def _chunk_python(source: str, file_path: str) -> list[Chunk]:
    lines = source.splitlines()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [Chunk(symbol=Path(file_path).stem, chunk_type="class",
                      start_line=1, end_line=len(lines), content=source, file_path=file_path,
                      language="python")]

    chunks: list[Chunk] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Only top-level functions and methods (depth 1 or 2)
            end_line = node.end_lineno
            content = "\n".join(lines[node.lineno - 1 : end_line])
            chunk_type: ChunkType = "method" if _is_method(node, tree) else "method"
            # Top-level functions get type "function", methods get "method"
            chunk_type = "function" if _is_top_level(node, tree) else "method"
            chunks.append(Chunk(
                symbol=node.name,
                chunk_type=chunk_type,
                start_line=node.lineno,
                end_line=end_line,
                content=content,
                file_path=file_path,
                language="python",
            ))

    return chunks


def _is_top_level(func_node: ast.AST, tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for child in ast.walk(node):
                if child is func_node:
                    return False
    return True


def _is_method(func_node: ast.AST, tree: ast.Module) -> bool:
    return not _is_top_level(func_node, tree)


# ---------------------------------------------------------------------------
# TypeScript chunker (regex-based)
# ---------------------------------------------------------------------------

_TS_METHOD_RE = re.compile(
    r'^[ \t]*(?:(?:public|private|protected|static|async|override|abstract)\s+)*'
    r'([a-zA-Z_$][a-zA-Z0-9_$]*)\s*[<(]',
    re.MULTILINE,
)
_TS_FUNCTION_RE = re.compile(
    r'^(?:export\s+)?(?:async\s+)?function\s+([a-zA-Z_$][a-zA-Z0-9_$]*)\s*[<(]',
    re.MULTILINE,
)
_TS_CLASS_RE = re.compile(
    r'^(?:export\s+)?(?:abstract\s+)?class\s+([a-zA-Z_$][a-zA-Z0-9_$]*)',
    re.MULTILINE,
)
_SKIP_TS_NAMES = {
    "constructor", "if", "for", "while", "switch", "catch", "return",
    "new", "typeof", "instanceof", "delete", "void", "throw",
}


def _chunk_typescript(source: str, file_path: str) -> list[Chunk]:
    lines = source.splitlines()
    chunks: list[Chunk] = []

    # Top-level exported functions
    for m in _TS_FUNCTION_RE.finditer(source):
        name = m.group(1)
        if name in _SKIP_TS_NAMES:
            continue
        start_line = source[:m.start()].count("\n") + 1
        end_line = _find_block_end(lines, start_line - 1)
        content = "\n".join(lines[start_line - 1 : end_line])
        chunks.append(Chunk(
            symbol=name,
            chunk_type="method",
            start_line=start_line,
            end_line=end_line,
            content=content,
            file_path=file_path,
            language="typescript",
        ))

    # Class methods
    for m in _TS_METHOD_RE.finditer(source):
        name = m.group(1)
        if name in _SKIP_TS_NAMES or name[0].isupper():
            continue
        line_text = source[:m.start()].count("\n")
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
        chunks.append(Chunk(
            symbol=name,
            chunk_type="method",
            start_line=start_line,
            end_line=end_line,
            content=content,
            file_path=file_path,
            language="typescript",
        ))

    if not chunks:
        chunks.append(Chunk(
            symbol=Path(file_path).stem,
            chunk_type="class",
            start_line=1,
            end_line=len(lines),
            content=source,
            file_path=file_path,
            language="typescript",
        ))

    return chunks


def _find_block_end(lines: list[str], start_idx: int) -> int:
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
            chunks.append(Chunk(
                symbol=Path(file_path).stem,
                chunk_type="class",
                start_line=1,
                end_line=len(lines),
                content=source,
                file_path=file_path,
            ))
        return chunks
    elif language == "python":
        return _chunk_python(source, file_path)
    elif language in ("typescript", "ts"):
        return _chunk_typescript(source, file_path)
    else:
        lines = source.splitlines()
        return [Chunk(
            symbol=Path(file_path).stem,
            chunk_type="class",
            start_line=1,
            end_line=len(lines),
            content=source,
            file_path=file_path,
            language=language,
        )]

