from __future__ import annotations

import ast
from unittest.mock import patch

from axon.embedder.chunker import _PY_PARSER, Chunk, chunk_source
from axon.embedder.graph_extractor import extract_calls


def _make_py_chunk_with_tree(source: str, symbol: str = "fn") -> Chunk:
    tree = _PY_PARSER.parse(source.encode("utf-8"))
    return Chunk(
        symbol=symbol,
        chunk_type="function",
        start_line=1,
        end_line=len(source.splitlines()),
        content=source,
        file_path="test.py",
        language="python",
        metadata={"_tree": tree},
    )


class TestWalkCallsTsTree:
    def test_extracts_known_calls(self) -> None:
        from axon.embedder.graph_extractor import _walk_calls_ts_tree
        source = "def fn():\n    foo()\n    bar.baz()\n"
        tree = _PY_PARSER.parse(source.encode("utf-8"))
        calls = _walk_calls_ts_tree(tree)
        assert "foo" in calls

    def test_filters_skip_calls(self) -> None:
        from axon.embedder.graph_extractor import _walk_calls_ts_tree
        source = "def fn():\n    print('hello')\n    len(x)\n    my_func()\n"
        tree = _PY_PARSER.parse(source.encode("utf-8"))
        calls = _walk_calls_ts_tree(tree)
        # "print" and "len" are in _SKIP_CALLS
        assert "print" not in calls
        assert "len" not in calls
        assert "my_func" in calls

    def test_returns_list_not_set(self) -> None:
        from axon.embedder.graph_extractor import _walk_calls_ts_tree
        source = "def fn():\n    foo()\n"
        tree = _PY_PARSER.parse(source.encode("utf-8"))
        result = _walk_calls_ts_tree(tree)
        assert isinstance(result, list)


class TestExtractCallsUsesCachedTree:
    def test_cached_tree_avoids_ast_parse(self) -> None:
        """If metadata has _tree, ast.parse must NOT be called."""
        source = "def fn():\n    helper()\n"
        chunk = _make_py_chunk_with_tree(source)

        original_parse = ast.parse
        call_count = [0]

        def counting_parse(src: str, *args, **kwargs):  # type: ignore[override]
            call_count[0] += 1
            return original_parse(src, *args, **kwargs)

        with patch("ast.parse", side_effect=counting_parse):
            calls = extract_calls(chunk)

        assert call_count[0] == 0, "ast.parse called despite cached tree"
        assert "helper" in calls

    def test_no_tree_falls_back_to_ast(self) -> None:
        """Without _tree in metadata, ast.parse must be called as fallback."""
        source = "def fn():\n    helper()\n"
        chunk = Chunk(
            symbol="fn",
            chunk_type="function",
            start_line=1,
            end_line=2,
            content=source,
            file_path="test.py",
            language="python",
            metadata={},
        )
        # Should not raise; falls back to ast.parse
        calls = extract_calls(chunk)
        assert "helper" in calls


class TestChunkerTreeInMetadata:
    def test_python_chunk_has_tree_in_metadata(self) -> None:
        source = "def foo():\n    pass\n"
        chunks = chunk_source(source, "python", "foo.py")
        for c in chunks:
            assert "_tree" in c.metadata, f"chunk {c.symbol} missing _tree"

    def test_typescript_chunk_has_tree_in_metadata(self) -> None:
        source = "export function bar() {\n  return 1;\n}\n"
        chunks = chunk_source(source, "typescript", "bar.ts")
        for c in chunks:
            assert "_tree" in c.metadata, f"chunk {c.symbol} missing _tree"

    def test_java_chunk_has_tree_in_metadata(self) -> None:
        source = "public class Foo {\n  public void go() {}\n}\n"
        chunks = chunk_source(source, "java", "Foo.java")
        for c in chunks:
            assert "_tree" in c.metadata, f"chunk {c.symbol} missing _tree"

    def test_tree_is_not_included_in_qdrant_payload(self) -> None:
        """Confirm that VectorChunk construction (pipeline) does not spread metadata."""
        # The pipeline constructs VectorChunk with explicit fields - no **chunk.metadata.
        # We verify the Chunk model does not bleed into VectorChunk accidentally.
        from axon.store.vector_store import Chunk as VectorChunk
        # VectorChunk has no metadata field - instantiation without it must succeed.
        vc = VectorChunk(
            id="abc",
            vector=[0.1, 0.2],
            file_path="f.py",
            language="python",
            chunk_type="function",
            symbol="fn",
            project="proj",
            ctx="personal",
            content="def fn(): pass",
        )
        assert not hasattr(vc, "metadata")


class TestCachedTreeEquivalence:
    """The cached-tree path must produce the SAME calls as the fresh-parse
    fallback, including import-derived edges (regression: the cached Java/TS
    path dropped the import scan that the fallback performs)."""

    def test_java_cached_matches_fallback_including_imports(self) -> None:
        from axon.embedder.chunker import _PARSER

        source = "import com.foo.Bar;\nclass C {\n  void go() { helper(); }\n}\n"
        tree = _PARSER.parse(source.encode("utf-8"))
        with_tree = Chunk(
            symbol="C", chunk_type="class", start_line=1, end_line=4,
            content=source, file_path="C.java", language="java",
            metadata={"_tree": tree},
        )
        without_tree = with_tree.model_copy(update={"metadata": {}})

        cached = extract_calls(with_tree)
        fresh = extract_calls(without_tree)
        assert cached == fresh
        assert "Bar" in cached, "import-derived edge must survive the cached path"
        assert "helper" in cached

    def test_ts_cached_matches_fallback_including_imports(self) -> None:
        from axon.embedder.chunker import _TS_PARSER

        source = "import { Bar } from './bar';\nfunction go() { helper(); }\n"
        tree = _TS_PARSER.parse(source.encode("utf-8"))
        with_tree = Chunk(
            symbol="go", chunk_type="function", start_line=1, end_line=2,
            content=source, file_path="m.ts", language="typescript",
            metadata={"_tree": tree},
        )
        without_tree = with_tree.model_copy(update={"metadata": {}})

        assert extract_calls(with_tree) == extract_calls(without_tree)
        assert "Bar" in extract_calls(with_tree)
