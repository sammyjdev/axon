"""Tree-sitter-backed extraction tests for TypeScript and Java calls.

The regex extractor _METHOD_CALL_RE casava qualquer ``.foo(`` no
source — incluindo strings, comentários e tipos genéricos. Os testes
abaixo cobrem casos que o regex pegava errado e tree-sitter corrige.
"""

from __future__ import annotations

from axon.embedder.chunker import Chunk
from axon.embedder.graph_extractor import build_dependency_records, extract_calls


def _chunk(symbol: str, content: str, language: str) -> Chunk:
    return Chunk(
        symbol=symbol,
        chunk_type="method",
        start_line=1,
        end_line=len(content.splitlines()) or 1,
        content=content,
        file_path=f"/tmp/{symbol}.{language}",
        language=language,
    )


class TestTSStringsAreNotCalls:
    def test_method_name_inside_string_literal_is_ignored(self) -> None:
        """Regex matched `.fetch(` inside a string. Tree-sitter does not."""
        chunk = _chunk(
            "render",
            'export const render = () => {\n'
            '  const msg = "call .fetch() to retrieve";\n'
            '  api.fetch();\n'
            '};\n',
            "typescript",
        )
        calls = extract_calls(chunk)
        # The real call ``api.fetch()`` stays; only one occurrence
        assert calls.count("fetch") == 1

    def test_method_name_in_template_literal_ignored(self) -> None:
        chunk = _chunk(
            "log",
            'export const log = () => {\n'
            '  console.log(`will run .commit() next`);\n'
            '};\n',
            "typescript",
        )
        calls = extract_calls(chunk)
        # ``commit`` only appears inside template literal — should not be a call
        assert "commit" not in calls

    def test_method_in_line_comment_ignored(self) -> None:
        chunk = _chunk(
            "doit",
            'function doit() {\n'
            '  // legacy: previously called .deprecated()\n'
            '  process();\n'
            '}\n',
            "typescript",
        )
        calls = extract_calls(chunk)
        assert "deprecated" not in calls
        assert "process" in calls


class TestJavaStringsAreNotCalls:
    def test_string_literal_does_not_yield_call(self) -> None:
        chunk = _chunk(
            "Controller.handle",
            'log.info("did not invoke .deprecatedThing()");\n'
            'save();\n',
            "java",
        )
        calls = extract_calls(chunk)
        assert "deprecatedThing" not in calls
        assert "save" in calls

    def test_javadoc_comment_ignored(self) -> None:
        chunk = _chunk(
            "Service.run",
            '/**\n'
            ' * Replaces .legacyCall() with the new pipeline.\n'
            ' */\n'
            'public void run() {\n'
            '    pipeline();\n'
            '}\n',
            "java",
        )
        calls = extract_calls(chunk)
        assert "legacyCall" not in calls
        assert "pipeline" in calls


class TestKeywordsStillFiltered:
    def test_typescript_keywords_skipped(self) -> None:
        chunk = _chunk(
            "do",
            'function do_() {\n'
            '  if (true) { for (;;) {} }\n'
            '  realCall();\n'
            '}\n',
            "typescript",
        )
        calls = extract_calls(chunk)
        # Keywords would have matched regex; should not appear
        assert "if" not in calls
        assert "for" not in calls
        assert "realCall" in calls


class TestSimpleCallsStillWork:
    """Sanity: don't regress the simple cases the regex already handled."""

    def test_simple_typescript_method_call(self) -> None:
        chunk = _chunk(
            "use",
            'function use() {\n'
            '  api.fetch();\n'
            '  hydrate();\n'
            '}\n',
            "typescript",
        )
        calls = extract_calls(chunk)
        assert "fetch" in calls
        assert "hydrate" in calls

    def test_simple_java_method_call(self) -> None:
        chunk = _chunk(
            "Controller.handle",
            'validator.check();\n'
            'save();\n',
            "java",
        )
        calls = extract_calls(chunk)
        assert "check" in calls
        assert "save" in calls

    def test_build_dependency_records_still_links_calls(self) -> None:
        """End-to-end dependency record building still works."""
        chunks = [
            _chunk(
                "render",
                'export const render = () => {\n  hydrate();\n  api.fetch();\n};\n',
                "typescript",
            ),
            _chunk("hydrate", "export function hydrate() {\n  return true;\n}\n", "typescript"),
            _chunk("fetch", "export function fetch() {\n  return true;\n}\n", "typescript"),
        ]
        records = {r.symbol: (r.calls, r.called_by) for r in build_dependency_records(chunks)}
        assert "fetch" in records["render"][0]
        assert "hydrate" in records["render"][0]
