from __future__ import annotations

import ast
import re

import tree_sitter_java as tsjava
from pydantic import BaseModel, ConfigDict
from tree_sitter import Language, Node, Parser

from axon.embedder.chunker import (
    _TS_PARSER,
    _TSX_PARSER,
    Chunk,
)

_JAVA_LANG = Language(tsjava.language())
_JAVA_CALL_PARSER = Parser(_JAVA_LANG)

# Retained for the import-statement scan that tree-sitter doesn't simplify
# enough to justify reimplementing per language.
_IMPORT_RE = re.compile(
    r"^\s*(?:import|from)\s+([A-Za-z_$][A-Za-z0-9_$.]*)|^\s*import\s+.*\{([^}]+)\}",
    re.MULTILINE,
)
_SKIP_CALLS = {
    "if",
    "for",
    "while",
    "switch",
    "catch",
    "return",
    "new",
    "throw",
    "typeof",
    "sizeof",
    "print",
    "len",
    "str",
    "int",
    "float",
    "list",
    "dict",
    "set",
    "super",
    "this",
}


class DependencyRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    calls: list[str]
    called_by: list[str]


def build_dependency_records(chunks: list[Chunk]) -> list[DependencyRecord]:
    calls_by_symbol: dict[str, set[str]] = {}
    called_by: dict[str, set[str]] = {}

    for chunk in chunks:
        calls = extract_calls(chunk)
        calls_by_symbol.setdefault(chunk.symbol, set()).update(calls)
        called_by.setdefault(chunk.symbol, set())
        for target in calls:
            called_by.setdefault(target, set()).add(chunk.symbol)

    symbols = set(calls_by_symbol) | set(called_by)
    return [
        DependencyRecord(
            symbol=symbol,
            calls=sorted(calls_by_symbol.get(symbol, set())),
            called_by=sorted(called_by.get(symbol, set())),
        )
        for symbol in sorted(symbols)
    ]


def extract_calls(chunk: Chunk) -> list[str]:
    cached_tree = chunk.metadata.get("_tree")
    if chunk.language == "python":
        if cached_tree is not None:
            raw_calls = _walk_calls_ts_tree(cached_tree)
        else:
            raw_calls = _extract_python_calls(chunk.content)
    elif chunk.language == "java":
        if cached_tree is not None:
            _java_calls: set[str] = set()
            _walk_calls(cached_tree.root_node, _java_calls)
            raw_calls = sorted(c for c in _java_calls if c and c not in _SKIP_CALLS)
        else:
            raw_calls = _extract_ts_or_java_calls(chunk.content, _JAVA_CALL_PARSER)
    elif chunk.language in {"typescript", "ts"}:
        if cached_tree is not None:
            _ts_calls: set[str] = set()
            _walk_calls(cached_tree.root_node, _ts_calls)
            raw_calls = sorted(c for c in _ts_calls if c and c not in _SKIP_CALLS)
        else:
            parser = _TSX_PARSER if chunk.file_path.endswith(".tsx") else _TS_PARSER
            raw_calls = _extract_ts_or_java_calls(chunk.content, parser)
    else:
        raw_calls = []
    return sorted(call for call in raw_calls if call != chunk.symbol)


def _extract_python_calls(source: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    calls: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _python_call_name(node.func)
        if name and name not in _SKIP_CALLS:
            calls.add(name)
    return sorted(calls)


def _walk_calls_ts_tree(tree: object) -> list[str]:
    """Extract call names from a tree-sitter Tree for Python source.

    The Python tree-sitter grammar uses 'call' nodes (not 'call_expression').
    The callee is accessed via the 'function' field; for attribute calls like
    obj.method(), the attribute name is extracted from the attribute child.
    """
    from tree_sitter import Node as TsNode

    calls: set[str] = set()

    def _visit(node: TsNode) -> None:
        if node.type == "call":
            fn_node = node.child_by_field_name("function")
            if fn_node is not None:
                if fn_node.type == "identifier":
                    name = fn_node.text.decode("utf-8", errors="replace")
                    if name and name not in _SKIP_CALLS:
                        calls.add(name)
                elif fn_node.type == "attribute":
                    attr = fn_node.child_by_field_name("attribute")
                    if attr is None:
                        # fallback: last identifier child
                        for child in fn_node.children:
                            if child.type == "identifier":
                                attr = child
                    if attr is not None:
                        name = attr.text.decode("utf-8", errors="replace")
                        if name and name not in _SKIP_CALLS:
                            calls.add(name)
        for child in node.children:
            _visit(child)

    _visit(tree.root_node)
    return sorted(calls)


def _python_call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _extract_ts_or_java_calls(source: str, parser: Parser) -> list[str]:
    """Extract callee names via tree-sitter, ignoring strings and comments.

    The regex predecessor matched any `.foo(` substring — including
    occurrences inside string literals, template literals, line and
    block comments, and Javadoc. Tree-sitter visits only real AST
    nodes, so strings/comments are naturally excluded.

    Falls back to an empty list if parsing fails (defensive — has not
    been observed in practice with mature grammars).
    """
    try:
        tree = parser.parse(source.encode("utf-8"))
    except Exception:
        return []

    calls: set[str] = set()
    _walk_calls(tree.root_node, calls)

    # Imports still come from a simple regex — they are line-oriented
    # and not represented uniformly across Java/TS grammars.
    for match in _IMPORT_RE.finditer(source):
        imported = match.group(1) or match.group(2) or ""
        for part in imported.replace(",", " ").split():
            calls.add(part.rsplit(".", 1)[-1].strip())

    return sorted(call for call in calls if call and call not in _SKIP_CALLS)


def _walk_calls(node: Node, calls: set[str]) -> None:
    """Visit AST nodes, harvesting callee names from real call sites."""
    t = node.type
    # TypeScript / JavaScript
    if t == "call_expression":
        callee = node.child_by_field_name("function")
        if callee is not None:
            name = _ts_callee_name(callee)
            if name:
                calls.add(name)
    # Java
    elif t == "method_invocation":
        name_node = node.child_by_field_name("name")
        if name_node is not None:
            calls.add(name_node.text.decode("utf-8", errors="replace"))
    elif t == "object_creation_expression":
        # Java `new Foo()` — record the type name
        type_node = node.child_by_field_name("type")
        if type_node is not None:
            calls.add(type_node.text.decode("utf-8", errors="replace"))

    for child in node.children:
        _walk_calls(child, calls)


def _ts_callee_name(node: Node) -> str | None:
    """Resolve the call target's symbol name for TS/JS AST nodes."""
    if node.type == "identifier":
        return node.text.decode("utf-8", errors="replace")
    if node.type == "member_expression":
        prop = node.child_by_field_name("property")
        if prop is not None:
            return prop.text.decode("utf-8", errors="replace")
    return None
