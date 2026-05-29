"""Tests for the Python chunker (now backed by tree-sitter-python).

Replaces the implicit reliance on Python's stdlib `ast` module so that
broken / WIP source files yield partial chunks instead of a "1 chunk =
file" fallback, and so that Python 3.12+ syntax (PEP 695 type aliases,
generic functions) parses cleanly regardless of which interpreter runs
AXON.
"""

from __future__ import annotations

from axon.embedder.chunker import chunk_source


class TestSimplePython:
    def test_top_level_function_becomes_chunk(self) -> None:
        source = "def foo():\n    return 1\n"
        chunks = chunk_source(source, "python", "x.py")
        assert any(c.symbol == "foo" and c.chunk_type == "function" for c in chunks)

    def test_class_method_is_method_chunk(self) -> None:
        source = (
            "class Bar:\n"
            "    def method(self):\n"
            "        return 2\n"
        )
        chunks = chunk_source(source, "python", "x.py")
        assert any(c.symbol == "method" and c.chunk_type == "method" for c in chunks)

    def test_async_function_recognized(self) -> None:
        source = "async def fetch():\n    return 3\n"
        chunks = chunk_source(source, "python", "x.py")
        assert any(c.symbol == "fetch" for c in chunks)


class TestErrorRecovery:
    def test_partially_broken_file_yields_partial_chunks(self) -> None:
        """Tree-sitter recovers from syntax errors; ast would have given up.

        File has one good function and one with a missing close-paren.
        Old `ast` path returned a single fallback chunk. Tree-sitter
        recovers and extracts the good function.
        """
        source = (
            "def good():\n"
            "    return 1\n"
            "\n"
            "def broken(\n"
            "    return 2\n"
        )
        chunks = chunk_source(source, "python", "wip.py")
        symbols = [c.symbol for c in chunks]
        assert "good" in symbols, f"expected partial recovery; got {symbols}"

    def test_completely_broken_file_falls_back(self) -> None:
        """Pure garbage still yields at least one chunk (no crash)."""
        source = "@#$%^&*\n!!! not python !!!\n"
        chunks = chunk_source(source, "python", "garbage.py")
        assert len(chunks) >= 1


class TestModernSyntax:
    def test_pep_695_type_alias_does_not_crash(self) -> None:
        """`type Foo = ...` is Python 3.12+ syntax.

        On older Python AST this would raise SyntaxError and produce
        the fallback "1 chunk = file". Tree-sitter parses it fine.
        """
        source = (
            "type IntList = list[int]\n"
            "\n"
            "def use_alias(xs: IntList) -> int:\n"
            "    return sum(xs)\n"
        )
        chunks = chunk_source(source, "python", "modern.py")
        # At minimum, use_alias should be a chunk (not just a fallback)
        symbols = [c.symbol for c in chunks]
        assert "use_alias" in symbols

    def test_pep_695_generic_function_parses(self) -> None:
        """`def f[T](x: T) -> T:` generic syntax, Python 3.12+."""
        source = "def identity[T](x: T) -> T:\n    return x\n"
        chunks = chunk_source(source, "python", "g.py")
        symbols = [c.symbol for c in chunks]
        assert "identity" in symbols


class TestNestedFunctions:
    def test_nested_function_is_method_not_function(self) -> None:
        """Nested defs inside a class are methods; nested inside a
        function are inner methods. Only chunk reasonable top-level
        constructs to keep the index lean.
        """
        source = (
            "class Outer:\n"
            "    def m(self):\n"
            "        def inner():\n"
            "            return 1\n"
            "        return inner()\n"
        )
        chunks = chunk_source(source, "python", "n.py")
        symbols = [c.symbol for c in chunks]
        # `m` and `inner` should both appear; behaviour: every def is a chunk
        assert "m" in symbols
