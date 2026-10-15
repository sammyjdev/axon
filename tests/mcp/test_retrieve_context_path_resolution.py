"""ID-13 through the real MCP entry point.

`test_hit_path_resolution.py` injects `roots=` into `_build_context_pack`, so it cannot see
whether `_retrieve_context` feeds it the roots of this machine. These tests go through
`_retrieve_context` with only the module runtime replaced.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from axon.mcp import server


class _DummyEmbedder:
    def embed_one(self, text: str) -> list[float]:
        return [0.0] * 1024


class _NewRowStore:
    async def search(self, **kwargs: object) -> list[dict]:
        return [
            {
                "score": 0.9,
                "payload": {
                    "content": "def run_job(): pass",
                    "file_path": "src/x.py",
                    "project": "repo_a",
                },
            }
        ]


async def _first_segment(
    engine: Path, monkeypatch: pytest.MonkeyPatch
) -> str:
    monkeypatch.setenv("AXON_ENGINE", str(engine))
    monkeypatch.setenv("AXON_RERANK", "0")
    monkeypatch.setattr(server, "_get_embedder", lambda: _DummyEmbedder())
    monkeypatch.setattr(server, "_get_vector_store", lambda: _NewRowStore())
    monkeypatch.setattr(
        server,
        "_RUNTIME",
        dataclasses.replace(server._RUNTIME, engine_root=engine, vault_root=engine / "vault"),
    )
    _, pack, _ = await server._retrieve_context(
        query="run job",
        ctx="knowledge",
        language=None,
        max_depth=1,
        max_nodes=5,
        max_tokens=1000,
    )
    return pack.segments[0]


@pytest.mark.asyncio
async def test_retrieve_context_resolves_a_new_row_against_this_machines_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_a = tmp_path / "checkouts" / "repo_a"
    (repo_a / "src").mkdir(parents=True)
    (repo_a / "src" / "x.py").write_text("def run_job(): pass\n", encoding="utf-8")
    engine = tmp_path / "engine"
    (engine / "data").mkdir(parents=True)
    (engine / "data" / "onboarded_repos.json").write_text(
        json.dumps([str(repo_a)]), encoding="utf-8"
    )

    segment = await _first_segment(engine, monkeypatch)

    assert f"Arquivo: {(repo_a / 'src' / 'x.py').as_posix()}\n" in segment


@pytest.mark.asyncio
async def test_retrieve_context_marks_a_new_row_whose_repo_is_not_registered_here(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = tmp_path / "engine"
    (engine / "data").mkdir(parents=True)

    segment = await _first_segment(engine, monkeypatch)

    assert "Arquivo: repo_a:src/x.py (not on this machine)\n" in segment
