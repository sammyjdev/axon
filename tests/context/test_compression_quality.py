from __future__ import annotations

import pytest

from prometheus.context.compression_quality import (
    compression_contamination_note,
    compression_preservation_note,
    compression_quality_note,
    extract_required_symbols,
)


def test_compression_quality_accepts_plain_context() -> None:
    assert (
        compression_contamination_note("async def index_path(...) -> tuple[int, int]: ...") is None
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "[0.9] /tmp/a.py :: index_path :: async def index_path(): ...",
            ["index_path"],
        ),
        (
            "### _semantic_search_hits (python)\nArquivo: /tmp/b.py",
            ["_semantic_search_hits"],
        ),
        (
            "\n".join(
                [
                    "[0.9] /tmp/a.py :: index_path :: async def index_path(): ...",
                    "### _semantic_search_hits (python)",
                    "Arquivo: /tmp/b.py",
                ]
            ),
            ["index_path", "_semantic_search_hits"],
        ),
    ],
)
def test_extract_required_symbols_supports_cli_mcp_and_mixed_formats(
    source: str,
    expected: list[str],
) -> None:
    assert extract_required_symbols(source) == expected


def test_compression_quality_rejects_prompt_contamination() -> None:
    note = compression_contamination_note(
        "## Your task: Compress the provided Python code snippet."
    )

    assert note is not None
    assert "prompt contamination" in note


def test_compression_quality_rejects_missing_cli_symbols() -> None:
    source = "\n".join(
        [
            "[0.9] /tmp/a.py :: index_path :: async def index_path(): ...",
            "[0.8] /tmp/b.py :: _semantic_search_hits :: async def _semantic_search_hits(): ...",
        ]
    )
    compressed = "async def _semantic_search_hits(): ..."

    note = compression_preservation_note(source, compressed)

    assert note is not None
    assert "index_path" in note


def test_compression_quality_rejects_single_missing_symbol() -> None:
    source = "[0.9] /tmp/a.py :: index_path :: async def index_path(): ..."
    compressed = "nothing relevant here"

    note = compression_preservation_note(source, compressed)

    assert note is not None
    assert "index_path" in note


def test_compression_quality_accepts_preserved_cli_symbols() -> None:
    source = "\n".join(
        [
            "[0.9] /tmp/a.py :: index_path :: async def index_path(): ...",
            "[0.8] /tmp/b.py :: _semantic_search_hits :: async def _semantic_search_hits(): ...",
        ]
    )
    compressed = "index_path upserts chunks. _semantic_search_hits queries Qdrant."

    assert compression_quality_note(source, compressed) is None


def test_compression_quality_rejects_missing_mcp_symbols() -> None:
    source = "\n".join(
        [
            "### index_path (python)",
            "Arquivo: /tmp/a.py",
            "### _semantic_search_hits (python)",
            "Arquivo: /tmp/b.py",
        ]
    )
    compressed = "_semantic_search_hits queries Qdrant."

    note = compression_preservation_note(source, compressed)

    assert note is not None
    assert "index_path" in note
