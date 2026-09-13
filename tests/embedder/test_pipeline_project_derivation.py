# tests/embedder/test_pipeline_project_derivation.py
"""Tests for project derivation from repo root in embedder pipeline (Task 5).

Verifies:
- Indexing two files in directories with the same name (repo_a/tests/x.py and
  repo_b/tests/y.py) assigns different project identities (repo_a and repo_b)
  instead of the old directory name fragment 'tests'.
- Both write sites in pipeline.py (ingest_file and index_path) derive project
  from repo_identity.
- repo_identity is not called once per chunk; it is called once per file/directory
  and cached across chunks and sibling files.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import axon.embedder.pipeline as pipeline_mod
from axon.store.pg_vector_store import VECTOR_SIZE, PgVectorStore


def _get_test_pg_url() -> str:
    env = os.environ.get("AXON_PG_URL")
    if env:
        return env
    from axon.config.runtime import load_runtime_config

    return load_runtime_config().pg_url


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(  # noqa: S603
        ["git", *args], cwd=cwd, check=True, capture_output=True  # noqa: S603, S607
    )


def _init_git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-b", "main"], cwd=path)
    _git(["config", "user.email", "test@axon.dev"], cwd=path)
    _git(["config", "user.name", "AXON Test"], cwd=path)
    return path


class _NullCache:
    """Minimal no-op FileCache."""

    async def get_all_sha1s(
        self, ctx: str, *, chunker_version: str | None = None
    ) -> dict[str, str]:
        return {}

    async def set_entry(
        self, fp, ctx, sha1, cc, *, status: str = "done", chunker_version: str | None = None
    ) -> None:
        pass

    async def delete_entry(self, fp, ctx) -> None:
        pass

    async def list_entries(self, ctx: str) -> list[tuple[str, str]]:
        return []


def _mock_engine() -> MagicMock:
    engine = MagicMock()
    engine.embed.side_effect = lambda texts: [[0.0] * VECTOR_SIZE for _ in texts]
    return engine


@pytest.mark.asyncio
async def test_index_path_assigns_different_projects_for_same_named_subdirs(
    tmp_path: Path,
) -> None:
    """The whole task: repo_a/tests/x.py and repo_b/tests/y.py must get different projects.

    Under the old parent.name behavior, both files were keyed under project='tests'.
    Under the repo_identity derivation, they are keyed under 'repo_a' and 'repo_b'.
    """
    repo_a = _init_git_repo(tmp_path / "repo_a")
    repo_b = _init_git_repo(tmp_path / "repo_b")

    file_a = repo_a / "tests" / "x.py"
    file_b = repo_b / "tests" / "y.py"

    file_a.parent.mkdir(parents=True, exist_ok=True)
    file_a.write_text("def test_one():\n    assert 1 == 1\n", encoding="utf-8")
    _git(["add", "."], cwd=repo_a)
    _git(["commit", "-m", "init a"], cwd=repo_a)

    file_b.parent.mkdir(parents=True, exist_ok=True)
    file_b.write_text("def test_two():\n    assert 2 == 2\n", encoding="utf-8")
    _git(["add", "."], cwd=repo_b)
    _git(["commit", "-m", "init b"], cwd=repo_b)

    store = PgVectorStore(_get_test_pg_url())
    await store.ensure_collections()

    engine = _mock_engine()
    file_cache = _NullCache()

    # Index both repos via write site 2 (index_path)
    await pipeline_mod.index_path(
        repo_a,
        engine=engine,
        store=store,
        vault_root=tmp_path,
        file_cache=file_cache,
    )
    await pipeline_mod.index_path(
        repo_b,
        engine=engine,
        store=store,
        vault_root=tmp_path,
        file_cache=file_cache,
    )

    # Search each repo's stored chunks
    hits_a = await store.search(
        query_vector=[0.0] * VECTOR_SIZE,
        collections=["knowledge"],
        query="test_one",
        project="repo_a",
    )
    hits_b = await store.search(
        query_vector=[0.0] * VECTOR_SIZE,
        collections=["knowledge"],
        query="test_two",
        project="repo_b",
    )

    assert len(hits_a) >= 1
    assert all(h["payload"]["project"] == "repo_a" for h in hits_a)
    assert any("x.py" in h["payload"]["file_path"] for h in hits_a)

    assert len(hits_b) >= 1
    assert all(h["payload"]["project"] == "repo_b" for h in hits_b)
    assert any("y.py" in h["payload"]["file_path"] for h in hits_b)

    # Searching under the old 'tests' project returns nothing
    hits_tests = await store.search(
        query_vector=[0.0] * VECTOR_SIZE,
        collections=["knowledge"],
        query="test",
        project="tests",
    )
    assert len(hits_tests) == 0


@pytest.mark.asyncio
async def test_write_site_1_ingest_file_derives_project_from_repo_identity(
    tmp_path: Path,
) -> None:
    """Drive write site 1: ingest_file directly ingests a single file into the store."""
    repo = _init_git_repo(tmp_path / "repo_ingest")
    test_file = repo / "tests" / "test_sample.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def sample_fn():\n    return 'ok'\n", encoding="utf-8")
    _git(["add", "."], cwd=repo)
    _git(["commit", "-m", "init"], cwd=repo)

    store = PgVectorStore(_get_test_pg_url())
    await store.ensure_collections()

    engine = _mock_engine()
    count = await pipeline_mod.ingest_file(test_file, engine=engine, store=store)
    assert count >= 1

    hits = await store.search(
        query_vector=[0.0] * VECTOR_SIZE,
        collections=["knowledge"],
        query="sample_fn",
        project="repo_ingest",
    )
    assert len(hits) >= 1
    assert hits[0]["payload"]["project"] == "repo_ingest"
    assert "test_sample.py" in hits[0]["payload"]["file_path"]


@pytest.mark.asyncio
async def test_write_site_2_index_path_derives_project_from_repo_identity(
    tmp_path: Path,
) -> None:
    """Drive write site 2: index_path walks directories and batches chunk writes."""
    repo = _init_git_repo(tmp_path / "repo_batch_idx")
    file1 = repo / "src" / "alpha.py"
    file1.parent.mkdir(parents=True, exist_ok=True)
    file1.write_text("def alpha(): pass\n", encoding="utf-8")
    _git(["add", "."], cwd=repo)
    _git(["commit", "-m", "init"], cwd=repo)

    store = PgVectorStore(_get_test_pg_url())
    await store.ensure_collections()

    engine = _mock_engine()
    indexed_files, total_chunks = await pipeline_mod.index_path(
        repo,
        engine=engine,
        store=store,
        vault_root=tmp_path,
        file_cache=_NullCache(),
    )
    assert indexed_files == 1
    assert total_chunks >= 1

    hits = await store.search(
        query_vector=[0.0] * VECTOR_SIZE,
        collections=["knowledge"],
        query="alpha",
        project="repo_batch_idx",
    )
    assert len(hits) >= 1
    assert hits[0]["payload"]["project"] == "repo_batch_idx"


@pytest.mark.asyncio
async def test_repo_identity_not_called_once_per_chunk_in_ingest_file(
    tmp_path: Path,
) -> None:
    """ingest_file must call repo_identity once per file, not once per chunk."""
    repo = _init_git_repo(tmp_path / "repo_chunks")
    multi_chunk_file = repo / "tests" / "multi.py"
    multi_chunk_file.parent.mkdir(parents=True, exist_ok=True)
    multi_chunk_file.write_text(
        "def func_one():\n    return 1\n\n"
        "def func_two():\n    return 2\n\n"
        "def func_three():\n    return 3\n\n"
        "def func_four():\n    return 4\n",
        encoding="utf-8",
    )
    _git(["add", "."], cwd=repo)
    _git(["commit", "-m", "multi"], cwd=repo)

    store = PgVectorStore(_get_test_pg_url())
    await store.ensure_collections()

    engine = _mock_engine()

    with patch.object(pipeline_mod, "repo_identity", wraps=pipeline_mod.repo_identity) as spy:
        chunks_count = await pipeline_mod.ingest_file(multi_chunk_file, engine=engine, store=store)
        assert chunks_count == 4
        # Crucial assertion: called exactly once, NOT once per chunk (4 times)
        assert spy.call_count == 1


@pytest.mark.asyncio
async def test_repo_identity_cached_per_directory_in_index_path(
    tmp_path: Path,
) -> None:
    """index_path caches repo_identity per directory across multiple files and chunks."""
    repo = _init_git_repo(tmp_path / "repo_cached")
    f1 = repo / "tests" / "f1.py"
    f2 = repo / "tests" / "f2.py"
    f1.parent.mkdir(parents=True, exist_ok=True)
    f1.write_text("def a1(): pass\ndef a2(): pass\n", encoding="utf-8")
    f2.write_text("def b1(): pass\ndef b2(): pass\n", encoding="utf-8")
    _git(["add", "."], cwd=repo)
    _git(["commit", "-m", "init"], cwd=repo)

    store = PgVectorStore(_get_test_pg_url())
    await store.ensure_collections()

    engine = _mock_engine()

    with patch.object(pipeline_mod, "repo_identity", wraps=pipeline_mod.repo_identity) as spy:
        indexed_files, total_chunks = await pipeline_mod.index_path(
            repo,
            engine=engine,
            store=store,
            vault_root=tmp_path,
            file_cache=_NullCache(),
        )
        assert indexed_files == 2
        assert total_chunks == 4
        # Even with 2 files and 4 chunks in repo/tests, repo_identity was called
        # once for the tests directory, proving directory-level caching in index_path.
        assert spy.call_count == 1


@pytest.mark.asyncio
async def test_index_path_project_cache_keyed_by_full_path_not_dir_name(
    tmp_path: Path,
) -> None:
    """index_path must key project_by_dir by full Path, not file_dir.name.

    When multiple repos are indexed in a single index_path call under a shared
    workspace root, subdirectories with identical names (repo_a/tests and
    repo_b/tests) share a project_by_dir cache lifetime. If project_by_dir
    were keyed by file_dir.name, repo_b/tests would collide with repo_a/tests
    and inherit project='repo_a'. Keying by full Path ensures repo_b/tests gets
    project='repo_b'.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    repo_a = _init_git_repo(workspace / "repo_a")
    repo_b = _init_git_repo(workspace / "repo_b")

    file_a = repo_a / "tests" / "x.py"
    file_b = repo_b / "tests" / "y.py"

    file_a.parent.mkdir(parents=True, exist_ok=True)
    file_a.write_text("def test_one():\n    assert 1 == 1\n", encoding="utf-8")
    _git(["add", "."], cwd=repo_a)
    _git(["commit", "-m", "init a"], cwd=repo_a)

    file_b.parent.mkdir(parents=True, exist_ok=True)
    file_b.write_text("def test_two():\n    assert 2 == 2\n", encoding="utf-8")
    _git(["add", "."], cwd=repo_b)
    _git(["commit", "-m", "init b"], cwd=repo_b)

    store = PgVectorStore(_get_test_pg_url())
    await store.ensure_collections()

    engine = _mock_engine()
    file_cache = _NullCache()

    # Index both repos in a SINGLE index_path call on workspace root
    indexed_files, total_chunks = await pipeline_mod.index_path(
        workspace,
        engine=engine,
        store=store,
        vault_root=tmp_path,
        file_cache=file_cache,
    )
    assert indexed_files == 2
    assert total_chunks >= 2

    # Search each repo's stored chunks
    hits_a = await store.search(
        query_vector=[0.0] * VECTOR_SIZE,
        collections=["knowledge"],
        query="test_one",
        project="repo_a",
    )
    hits_b = await store.search(
        query_vector=[0.0] * VECTOR_SIZE,
        collections=["knowledge"],
        query="test_two",
        project="repo_b",
    )

    assert len(hits_a) >= 1
    assert all(h["payload"]["project"] == "repo_a" for h in hits_a)
    assert any("x.py" in h["payload"]["file_path"] for h in hits_a)

    assert len(hits_b) >= 1
    assert all(h["payload"]["project"] == "repo_b" for h in hits_b)
    assert any("y.py" in h["payload"]["file_path"] for h in hits_b)

    # Cross-check: y.py must not be under repo_a, x.py must not be under repo_b
    hits_a_all = await store.search(
        query_vector=[0.0] * VECTOR_SIZE,
        collections=["knowledge"],
        query="test",
        project="repo_a",
    )
    hits_b_all = await store.search(
        query_vector=[0.0] * VECTOR_SIZE,
        collections=["knowledge"],
        query="test",
        project="repo_b",
    )
    assert any("x.py" in h["payload"]["file_path"] for h in hits_a_all)
    assert not any("y.py" in h["payload"]["file_path"] for h in hits_a_all)
    assert any("y.py" in h["payload"]["file_path"] for h in hits_b_all)
    assert not any("x.py" in h["payload"]["file_path"] for h in hits_b_all)

