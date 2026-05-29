"""Tests for the TypeScript chunker (now backed by tree-sitter-typescript).

Replaces the previous regex-based parser, which missed arrow function
exports, generic signatures, and accessors. Tree-sitter cleanly handles
JSX/TSX, generics, decorators and accessors, and recovers from partial
files mid-edit.
"""

from __future__ import annotations

from axon.embedder.chunker import chunk_source


class TestSimpleTypeScript:
    def test_top_level_function_chunked(self) -> None:
        source = "export function hello() {\n  return 'hi';\n}\n"
        chunks = chunk_source(source, "typescript", "x.ts")
        symbols = [c.symbol for c in chunks]
        assert "hello" in symbols

    def test_class_method_is_method_chunk(self) -> None:
        source = (
            "export class Greeter {\n"
            "  greet(name: string): string {\n"
            "    return `hi ${name}`;\n"
            "  }\n"
            "}\n"
        )
        chunks = chunk_source(source, "typescript", "x.ts")
        symbols = [c.symbol for c in chunks]
        assert "greet" in symbols
        greet = next(c for c in chunks if c.symbol == "greet")
        assert greet.chunk_type == "method"


class TestArrowFunctions:
    def test_arrow_function_assigned_to_const_is_chunked(self) -> None:
        """Regex parser missed this. Tree-sitter resolves the binding."""
        source = "export const handler = async (req: Request) => {\n  return req;\n};\n"
        chunks = chunk_source(source, "typescript", "h.ts")
        symbols = [c.symbol for c in chunks]
        assert "handler" in symbols

    def test_arrow_method_property(self) -> None:
        source = (
            "class Service {\n"
            "  process = (input: string): string => {\n"
            "    return input.toUpperCase();\n"
            "  };\n"
            "}\n"
        )
        chunks = chunk_source(source, "typescript", "s.ts")
        symbols = [c.symbol for c in chunks]
        assert "process" in symbols


class TestGenerics:
    def test_generic_function_signature(self) -> None:
        source = "export function identity<T>(x: T): T {\n  return x;\n}\n"
        chunks = chunk_source(source, "typescript", "g.ts")
        symbols = [c.symbol for c in chunks]
        assert "identity" in symbols

    def test_constrained_generic(self) -> None:
        source = (
            "export function pluck<T, K extends keyof T>(obj: T, key: K): T[K] {\n"
            "  return obj[key];\n"
            "}\n"
        )
        chunks = chunk_source(source, "typescript", "p.ts")
        symbols = [c.symbol for c in chunks]
        assert "pluck" in symbols


class TestAccessors:
    def test_getter_setter_are_methods(self) -> None:
        source = (
            "class Counter {\n"
            "  #n = 0;\n"
            "  get value(): number { return this.#n; }\n"
            "  set value(v: number) { this.#n = v; }\n"
            "}\n"
        )
        chunks = chunk_source(source, "typescript", "c.ts")
        symbols = [c.symbol for c in chunks]
        assert "value" in symbols


class TestErrorRecovery:
    def test_partially_broken_file_yields_chunks(self) -> None:
        source = (
            "export function good() {\n"
            "  return 1;\n"
            "}\n"
            "\n"
            "export function broken(\n"
            "  return 2;\n"
        )
        chunks = chunk_source(source, "typescript", "wip.ts")
        symbols = [c.symbol for c in chunks]
        assert "good" in symbols


class TestClassBoundary:
    def test_multiple_classes_chunked_separately(self) -> None:
        source = (
            "class A {\n  doA() { return 1; }\n}\n"
            "class B {\n  doB() { return 2; }\n}\n"
        )
        chunks = chunk_source(source, "typescript", "ab.ts")
        symbols = [c.symbol for c in chunks]
        assert "doA" in symbols
        assert "doB" in symbols
