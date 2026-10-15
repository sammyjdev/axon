"""search_code and `axon search` leave test files out unless asked for them."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from axon.mcp import server


class _Embedder:
    def embed_one(self, text: str) -> list[float]:
        return [0.0] * 1024


class _RecordingStore:
    def __init__(self) -> None:
        self.include_tests: list[object] = []

    async def search(self, **kwargs: object) -> list[dict]:
        self.include_tests.append(kwargs.get("include_tests"))
        return []


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _RecordingStore:
    monkeypatch.setenv("AXON_ENGINE", str(tmp_path))
    recording = _RecordingStore()
    monkeypatch.setattr(server, "_get_embedder", lambda: _Embedder())
    monkeypatch.setattr(server, "_get_vector_store", lambda: recording)
    return recording


async def test_search_code_leaves_test_files_out_unless_asked(store: _RecordingStore) -> None:
    await server.search_code("where is the budget trimmed")
    await server.search_code("where is the budget trimmed", include_tests=True)

    assert store.include_tests == [False, True]


def test_the_cli_search_asks_for_test_files_only_with_the_tests_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from axon.cli import pb

    seen: list[object] = []

    async def recording_hits(query: str, **kwargs: object) -> list[dict]:
        seen.append(kwargs.get("include_tests"))
        return []

    monkeypatch.setenv("AXON_ENGINE", str(tmp_path))
    monkeypatch.setattr(pb, "_semantic_search_hits", recording_hits)
    runner = CliRunner()

    runner.invoke(pb.app, ["search", "where is the budget trimmed"])
    runner.invoke(pb.app, ["search", "where is the budget trimmed", "--tests"])

    assert seen == [False, True]
