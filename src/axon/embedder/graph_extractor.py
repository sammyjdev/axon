from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from axon.embedder.chunker import Chunk

_CALL_RE = re.compile(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*\(")
_METHOD_CALL_RE = re.compile(r"\.([A-Za-z_$][A-Za-z0-9_$]*)\s*\(")
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


@dataclass(frozen=True)
class DependencyRecord:
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
    if chunk.language == "python":
        calls = _extract_python_calls(chunk.content)
    elif chunk.language in {"java", "typescript", "ts"}:
        calls = _extract_regex_calls(chunk.content)
    else:
        calls = []
    return sorted(call for call in calls if call != chunk.symbol)


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


def _python_call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _extract_regex_calls(source: str) -> list[str]:
    calls = set(_METHOD_CALL_RE.findall(source))
    calls.update(_CALL_RE.findall(source))
    for match in _IMPORT_RE.finditer(source):
        imported = match.group(1) or match.group(2) or ""
        for part in imported.replace(",", " ").split():
            calls.add(part.rsplit(".", 1)[-1].strip())
    return sorted(call for call in calls if call and call not in _SKIP_CALLS)
