from __future__ import annotations

from axon.embedder.chunker import _MAX_CHUNK_LINES, chunk_source


def _make_python_function(name: str, n_lines: int) -> str:
    """Generate a Python function with n_lines body lines."""
    body = "\n".join(f"    x_{i} = {i}" for i in range(n_lines - 1))
    return f"def {name}():\n{body}\n    return x_0\n"


def _make_ts_function(name: str, n_lines: int) -> str:
    body = "\n".join(f"  const x_{i} = {i};" for i in range(n_lines - 1))
    return f"export function {name}() {{\n{body}\n  return x_0;\n}}\n"


class TestPythonCap:
    def test_function_exactly_at_cap_is_single_chunk(self) -> None:
        # _make_python_function(n) yields n+1 physical lines; n=cap-1 -> exactly
        # _MAX_CHUNK_LINES physical lines, the strict boundary that stays single.
        src = _make_python_function("exact", _MAX_CHUNK_LINES - 1)
        chunks = chunk_source(src, "python", "f.py")
        fn_chunks = [c for c in chunks if c.symbol.startswith("exact")]
        assert len(fn_chunks) == 1
        c = fn_chunks[0]
        assert c.end_line - c.start_line + 1 == _MAX_CHUNK_LINES, "single chunk must be exactly at cap"

    def test_function_one_over_cap_splits(self) -> None:
        # n=cap -> cap+1 physical lines -> must split under the strict cap
        # (regression: the Python branch used to allow an 81-line single chunk).
        src = _make_python_function("oneover", _MAX_CHUNK_LINES)
        chunks = chunk_source(src, "python", "f.py")
        fn = [c for c in chunks if c.symbol.startswith("oneover")]
        assert len(fn) > 1
        for c in fn:
            assert c.end_line - c.start_line + 1 <= _MAX_CHUNK_LINES

    def test_function_below_cap_is_single_chunk(self) -> None:
        src = _make_python_function("small", 10)
        chunks = chunk_source(src, "python", "f.py")
        fn_chunks = [c for c in chunks if c.symbol.startswith("small")]
        assert len(fn_chunks) == 1

    def test_function_above_cap_splits(self) -> None:
        """A 200-line function must produce multiple chunks."""
        src = _make_python_function("big", 200)
        chunks = chunk_source(src, "python", "f.py")
        big_chunks = [c for c in chunks if c.symbol.startswith("big")]
        assert len(big_chunks) > 1, f"expected split, got {len(big_chunks)} chunks"

    def test_split_chunks_none_exceed_cap(self) -> None:
        src = _make_python_function("huge", 400)
        chunks = chunk_source(src, "python", "f.py")
        for c in chunks:
            size = c.end_line - c.start_line + 1
            assert size <= _MAX_CHUNK_LINES, f"chunk {c.symbol} has {size} lines > cap"

    def test_function_79_lines_is_single_chunk(self) -> None:
        src = _make_python_function("under", 79)
        chunks = chunk_source(src, "python", "f.py")
        fn = [c for c in chunks if c.symbol.startswith("under")]
        assert len(fn) == 1

    def test_function_81_lines_splits(self) -> None:
        src = _make_python_function("over", 81)
        chunks = chunk_source(src, "python", "f.py")
        fn = [c for c in chunks if c.symbol.startswith("over")]
        assert len(fn) > 1

    def test_split_python_subchunks_tagged_python(self) -> None:
        """Split Python functions must keep language="python" (regression:
        _split_large_node defaulted Chunk.language to "java")."""
        src = _make_python_function("polyglot", 400)
        chunks = chunk_source(src, "python", "f.py")
        split = [c for c in chunks if c.symbol.startswith("polyglot")]
        assert len(split) > 1
        assert all(c.language == "python" for c in split), [c.language for c in split]


class TestTypeScriptCap:
    def test_ts_function_above_cap_splits(self) -> None:
        src = _make_ts_function("bigTs", 200)
        chunks = chunk_source(src, "typescript", "f.ts")
        big = [c for c in chunks if c.symbol.startswith("bigTs")]
        assert len(big) > 1, f"expected split, got {len(big)}"

    def test_ts_split_none_exceed_cap(self) -> None:
        src = _make_ts_function("hugeTs", 400)
        chunks = chunk_source(src, "typescript", "f.ts")
        for c in chunks:
            size = c.end_line - c.start_line + 1
            assert size <= _MAX_CHUNK_LINES, f"chunk {c.symbol} exceeds cap with {size} lines"

    def test_ts_function_below_cap_is_single_chunk(self) -> None:
        src = _make_ts_function("smallTs", 20)
        chunks = chunk_source(src, "typescript", "f.ts")
        sm = [c for c in chunks if c.symbol.startswith("smallTs")]
        assert len(sm) == 1
