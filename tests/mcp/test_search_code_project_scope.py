# tests/mcp/test_search_code_project_scope.py
"""Tests for project scoping in MCP search_code tool (Task 5).

Verifies:
- search_code with project='proj_alpha' returns chunks belonging to proj_alpha
  and NO chunk whose file_path lies outside it.
- search_code with project='proj_beta' returns chunks belonging to proj_beta
  and NO chunk whose file_path lies outside it.
- search_code with repo='proj_alpha' behaves identically to project='proj_alpha'.
- Unscoped search returns chunks across projects.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import asyncpg
import pytest

from axon.mcp import server
from axon.observability.recall_telemetry import RecallTelemetryStore
from axon.router.classifier import TaskType
from axon.store.pg_vector_store import VECTOR_SIZE, PgVectorStore


def _get_test_pg_url() -> str:
    env = os.environ.get("AXON_PG_URL")
    if env:
        return env
    from axon.config.runtime import load_runtime_config

    return load_runtime_config().pg_url


class _FakeSessionStore:
    async def init(self) -> None:
        pass

    async def query_subgraph(self, *args, **kwargs) -> dict:
        return {"nodes": []}


@pytest.fixture(autouse=True)
def _isolate_telemetry(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AXON_RERANK", "0")
    telemetry_store = RecallTelemetryStore(runtime=SimpleNamespace(data_root=tmp_path))
    monkeypatch.setattr(server, "RecallTelemetryStore", lambda: telemetry_store)


async def _seed_multi_project_chunks(pg_url: str) -> None:
    store = PgVectorStore(pg_url)
    await store.ensure_collections()

    con = await asyncpg.connect(pg_url)
    try:
        await con.execute("TRUNCATE embeddings CASCADE")
        dummy_vec = f"[{','.join(['0.1'] * VECTOR_SIZE)}]"
        await con.execute(
            """
            INSERT INTO embeddings (
                id, vector, ctx, file_path, language, chunk_type, symbol, project, content
            )
            VALUES
            ('c-alpha', $1::vector, 'knowledge',
             '/repos/proj_alpha/src/alpha_service.py',
             'python', 'function', 'alpha_handler', 'proj_alpha',
             'def alpha_handler(): return "alpha result"'),
            ('c-beta', $1::vector, 'knowledge',
             '/repos/proj_beta/src/beta_service.py',
             'python', 'function', 'beta_handler', 'proj_beta',
             'def beta_handler(): return "beta result"')
            """,
            dummy_vec,
        )
    finally:
        await con.close()


@pytest.mark.asyncio
async def test_search_code_scoped_to_project_excludes_chunks_outside_it(
    monkeypatch,
) -> None:
    pg_url = _get_test_pg_url()
    await _seed_multi_project_chunks(pg_url)

    store = PgVectorStore(pg_url)
    monkeypatch.setattr(server, "_get_vector_store", lambda: store)
    monkeypatch.setattr(
        server, "_get_embedder", lambda: SimpleNamespace(embed_one=lambda q: [0.1] * VECTOR_SIZE)
    )
    monkeypatch.setattr(
        "axon.router.classifier.classify_task_with_source",
        lambda *a, **kw: (TaskType.CODE_ANALYSIS, "local"),
    )
    monkeypatch.setattr(server, "_get_session_store", lambda: _FakeSessionStore())
    monkeypatch.setattr(server, "_load_retrieval_profile", lambda: ("free", "auto", ()))

    # Search scoped to project 'proj_alpha'
    res_alpha = await server.search_code(
        query="alpha result",
        project="proj_alpha",
        ctx="knowledge",
    )
    assert "alpha_handler" in res_alpha
    assert "/repos/proj_alpha/src/alpha_service.py" in res_alpha
    # Explicit assertion: no chunk whose file_path lies outside proj_alpha is returned
    assert "beta_handler" not in res_alpha
    assert "/repos/proj_beta/src/beta_service.py" not in res_alpha
    assert "proj_beta" not in res_alpha

    # Search scoped to project 'proj_beta'
    res_beta = await server.search_code(
        query="beta result",
        project="proj_beta",
        ctx="knowledge",
    )
    assert "beta_handler" in res_beta
    assert "/repos/proj_beta/src/beta_service.py" in res_beta
    # Explicit assertion: no chunk whose file_path lies outside proj_beta is returned
    assert "alpha_handler" not in res_beta
    assert "/repos/proj_alpha/src/alpha_service.py" not in res_beta
    assert "proj_alpha" not in res_beta

    # Search using repo alias
    res_repo_alias = await server.search_code(
        query="alpha result",
        repo="proj_alpha",
        ctx="knowledge",
    )
    assert "alpha_handler" in res_repo_alias
    assert "beta_handler" not in res_repo_alias

    # Unscoped search returns both
    res_unscoped = await server.search_code(
        query="result",
        ctx="knowledge",
    )
    assert "alpha_handler" in res_unscoped
    assert "beta_handler" in res_unscoped
