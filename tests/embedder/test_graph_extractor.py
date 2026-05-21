from __future__ import annotations

from axon.embedder.chunker import Chunk
from axon.embedder.graph_extractor import build_dependency_records


def _chunk(symbol: str, content: str, language: str) -> Chunk:
    return Chunk(
        symbol=symbol,
        chunk_type="method",
        start_line=1,
        end_line=len(content.splitlines()) or 1,
        content=content,
        file_path=f"/tmp/{symbol}",
        language=language,
    )


def _records_by_symbol(chunks: list[Chunk]) -> dict[str, tuple[list[str], list[str]]]:
    return {
        record.symbol: (record.calls, record.called_by)
        for record in build_dependency_records(chunks)
    }


def test_build_dependency_records_links_simple_python_calls() -> None:
    records = _records_by_symbol(
        [
            _chunk(
                "handler",
                "def handler():\n    prepare()\n    client.commit()\n",
                "python",
            ),
            _chunk("prepare", "def prepare():\n    return 1\n", "python"),
            _chunk("commit", "def commit():\n    return None\n", "python"),
        ]
    )

    assert records["handler"] == (["commit", "prepare"], [])
    assert records["prepare"] == ([], ["handler"])
    assert records["commit"] == ([], ["handler"])


def test_build_dependency_records_links_simple_typescript_calls_with_regex() -> None:
    records = _records_by_symbol(
        [
            _chunk(
                "render",
                "export const render = () => {\n  hydrate();\n  api.fetch();\n};\n",
                "typescript",
            ),
            _chunk("hydrate", "export function hydrate() {\n  return true;\n}\n", "typescript"),
            _chunk("fetch", "export function fetch() {\n  return true;\n}\n", "typescript"),
        ]
    )

    assert records["render"] == (["fetch", "hydrate"], [])
    assert records["hydrate"] == ([], ["render"])
    assert records["fetch"] == ([], ["render"])


def test_build_dependency_records_links_simple_java_calls_with_regex() -> None:
    records = _records_by_symbol(
        [
            _chunk(
                "Controller.handle",
                "validator.check();\nsave();\n",
                "java",
            ),
            _chunk("Validator.check", "", "java"),
            _chunk("Repository.save", "", "java"),
        ]
    )

    assert records["Controller.handle"] == (["check", "save"], [])
    assert records["check"] == ([], ["Controller.handle"])
    assert records["save"] == ([], ["Controller.handle"])
