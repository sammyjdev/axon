"""`sync_root` brings one registered root to its HEAD (phase 1, SY-1 to SY-4)."""

from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from axon.core.file_identity import VAULT_REPO, RepoRoots, build_repo_roots
from axon.embedder import pipeline
from axon.embedder import sync as sync_module
from axon.embedder.sync import SyncCtxError, sync_root
from axon.store.pg_file_cache import PostgresFileCache
from axon.store.pg_vector_store import VECTOR_SIZE, PgVectorStore
from axon.store.vector_common import Chunk as VectorChunk


def _pg_url() -> str:
    env = os.environ.get("AXON_PG_URL")
    if env:
        return env
    from axon.config.runtime import load_runtime_config

    return load_runtime_config().pg_url


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True  # noqa: S607
    ).stdout.strip()


def _commit(repo: Path, name: str, body: str, message: str = "change") -> str:
    (repo / name).write_text(body, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


class _Bench:
    def __init__(
        self,
        repo: Path,
        roots: RepoRoots,
        store: PgVectorStore,
        cache: PostgresFileCache,
        vault: Path,
    ) -> None:
        self.repo = repo
        self.roots = roots
        self.store = store
        self.cache = cache
        self.vault = vault
        self.engine = MagicMock()
        self.engine.embed.side_effect = lambda texts: [[0.0] * VECTOR_SIZE for _ in texts]

    async def sync(self, ctx: str | None = "knowledge"):
        return await sync_root(
            self.repo,
            "myrepo",
            engine=self.engine,
            store=self.store,
            file_cache=self.cache,
            vault_root=self.vault,
            repo_roots=self.roots,
            ctx=ctx,
        )

    async def sync_vault(self):
        return await sync_root(
            self.vault,
            VAULT_REPO,
            engine=self.engine,
            store=self.store,
            file_cache=self.cache,
            vault_root=self.vault,
            repo_roots=self.roots,
            per_file_ctx=True,
        )

    async def vault_paths(self) -> set[str]:
        pool = await self.store._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT DISTINCT file_path FROM embeddings WHERE project = $1", VAULT_REPO
            )
        return {r["file_path"] for r in rows}

    async def indexed_paths(self) -> set[str]:
        pool = await self.store._ensure_pool()
        async with pool.acquire() as con:
            rows = await con.fetch(
                "SELECT DISTINCT file_path FROM embeddings"
                " WHERE project = 'myrepo' AND file_path NOT LIKE '/%'"
            )
        return {r["file_path"] for r in rows}

    async def content_of(self, name: str) -> str:
        pool = await self.store._ensure_pool()
        async with pool.acquire() as con:
            return await con.fetchval(
                "SELECT string_agg(content, '') FROM embeddings"
                " WHERE project = 'myrepo' AND file_path = $1",
                name,
            )

    async def legacy_row(self, ctx: str, name: str) -> None:
        await self.store.upsert_batch(
            [
                VectorChunk(
                    id=f"00000000-0000-0000-0000-{abs(hash((ctx, name))) % 10**12:012d}",
                    vector=[0.0] * VECTOR_SIZE,
                    file_path=f"/Users/someone/dev/myrepo/{name}",
                    language="markdown",
                    chunk_type="file",
                    symbol=name,
                    project="myrepo",
                    ctx=ctx,
                    content="legacy",
                )
            ]
        )


@pytest.fixture
async def bench(tmp_path: Path) -> AsyncGenerator[_Bench, None]:
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _commit(repo, "README.md", "# Title\nfirst\n")
    vault = tmp_path / "vault"
    store = PgVectorStore(_pg_url())
    await store.ensure_collections()
    cache = PostgresFileCache(_pg_url())
    await cache.ensure_schema()
    try:
        yield _Bench(repo, build_repo_roots([repo], vault), store, cache, vault)
    finally:
        await store.close()
        await cache.close()


async def test_a_commit_is_indexed_and_recorded(bench: _Bench) -> None:
    head = _commit(bench.repo, "notes.md", "# Notes\nbody\n")

    result = await bench.sync()

    assert result.commit == head
    assert await bench.cache.get_repo_state("myrepo") == ("knowledge", head)
    assert await bench.indexed_paths() == {"README.md", "notes.md"}


async def test_a_second_sync_at_the_same_head_embeds_nothing(bench: _Bench) -> None:
    await bench.sync()
    calls = bench.engine.embed.call_count

    await bench.sync()

    assert bench.engine.embed.call_count == calls


async def test_a_merge_converges_to_head(bench: _Bench) -> None:
    await bench.sync()
    _git(bench.repo, "switch", "-c", "feature")
    _commit(bench.repo, "feature.md", "# Feature\nbody\n")
    _git(bench.repo, "switch", "main")
    _commit(bench.repo, "main_only.md", "# Main\nbody\n")
    _git(bench.repo, "merge", "--no-ff", "-m", "merge feature", "feature")
    head = _git(bench.repo, "rev-parse", "HEAD")

    await bench.sync()

    assert await bench.cache.get_repo_state("myrepo") == ("knowledge", head)
    assert await bench.indexed_paths() == {"README.md", "feature.md", "main_only.md"}


async def test_a_rebase_converges_to_head(bench: _Bench) -> None:
    _git(bench.repo, "switch", "-c", "feature")
    _commit(bench.repo, "feature.md", "# Feature\nbody\n")
    await bench.sync()
    _git(bench.repo, "switch", "main")
    _commit(bench.repo, "main_only.md", "# Main\nbody\n")
    _git(bench.repo, "switch", "feature")
    _git(bench.repo, "rebase", "-q", "main")
    head = _git(bench.repo, "rev-parse", "HEAD")

    await bench.sync()

    assert await bench.cache.get_repo_state("myrepo") == ("knowledge", head)
    assert await bench.indexed_paths() == {"README.md", "feature.md", "main_only.md"}


async def test_a_rewritten_history_converges_and_drops_the_removed_file(
    bench: _Bench,
) -> None:
    _commit(bench.repo, "gone.md", "# Gone\nbody\n")
    await bench.sync()
    indexed = _git(bench.repo, "rev-parse", "HEAD")
    # The indexed commit stops being an ancestor of HEAD, as after a rebase.
    _git(bench.repo, "reset", "--hard", "HEAD~1")
    head = _commit(bench.repo, "kept.md", "# Kept\nbody\n")
    assert _git(bench.repo, "merge-base", indexed, head) != indexed

    await bench.sync()

    assert await bench.cache.get_repo_state("myrepo") == ("knowledge", head)
    assert await bench.indexed_paths() == {"README.md", "kept.md"}


async def test_head_moving_during_a_sync_still_ends_at_the_newer_head(
    bench: _Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_index_path = sync_module.index_path
    moved: list[str] = []

    async def index_then_commit(*args, **kwargs):
        out = await real_index_path(*args, **kwargs)
        if not moved:
            moved.append(_commit(bench.repo, "late.md", "# Late\nbody\n"))
        return out

    monkeypatch.setattr(sync_module, "index_path", index_then_commit)

    await bench.sync()

    assert await bench.cache.get_repo_state("myrepo") == ("knowledge", moved[0])
    assert "late.md" in await bench.indexed_paths()


async def test_head_moving_on_every_walk_still_ends_at_the_newest_head(
    bench: _Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_index_path = sync_module.index_path
    moved: list[str] = []

    async def index_then_commit(*args, **kwargs):
        out = await real_index_path(*args, **kwargs)
        if len(moved) < 2:
            moved.append(_commit(bench.repo, f"late{len(moved)}.md", "# Late\nbody\n"))
        return out

    monkeypatch.setattr(sync_module, "index_path", index_then_commit)

    await bench.sync()

    assert await bench.cache.get_repo_state("myrepo") == ("knowledge", moved[-1])
    assert {"late0.md", "late1.md"} <= await bench.indexed_paths()


async def test_a_later_sync_refuses_a_ctx_other_than_the_stored_one(bench: _Bench) -> None:
    await bench.sync(ctx="personal")
    state = await bench.cache.get_repo_state("myrepo")

    with pytest.raises(SyncCtxError):
        await bench.sync(ctx="knowledge")

    assert await bench.cache.get_repo_state("myrepo") == state


async def test_a_file_removed_while_the_walk_runs_does_not_stop_the_sync(
    bench: _Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    _commit(bench.repo, "doomed.md", "# Doomed\nbody\n")
    real_iter = pipeline.iter_supported_files
    removed: list[str] = []

    def list_then_remove(*args, **kwargs):
        files = list(real_iter(*args, **kwargs))
        if not removed:
            _git(bench.repo, "rm", "-q", "doomed.md")
            _git(bench.repo, "commit", "-m", "remove doomed")
            removed.append(_git(bench.repo, "rev-parse", "HEAD"))
        return files

    monkeypatch.setattr(pipeline, "iter_supported_files", list_then_remove)

    await bench.sync()

    assert await bench.cache.get_repo_state("myrepo") == ("knowledge", removed[0])
    assert await bench.indexed_paths() == {"README.md"}


async def test_a_directory_replaced_by_a_file_while_the_walk_runs_does_not_stop_the_sync(
    bench: _Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    (bench.repo / "docs.md").mkdir()
    _commit(bench.repo, "docs.md/note.md", "# Note\nbody\n")
    real_iter = pipeline.iter_supported_files
    replaced: list[str] = []

    def list_then_replace(*args, **kwargs):
        files = list(real_iter(*args, **kwargs))
        if not replaced:
            _git(bench.repo, "rm", "-q", "-r", "docs.md")
            replaced.append(_commit(bench.repo, "docs.md", "# Docs\nbody\n"))
        return files

    monkeypatch.setattr(pipeline, "iter_supported_files", list_then_replace)

    await bench.sync()

    assert await bench.cache.get_repo_state("myrepo") == ("knowledge", replaced[0])
    assert await bench.indexed_paths() == {"README.md", "docs.md"}


async def test_emptying_a_vault_folder_drops_it_from_the_index(bench: _Bench) -> None:
    note = bench.vault / "personal" / "n.md"
    note.parent.mkdir(parents=True)
    note.write_text("# N\nbody\n", encoding="utf-8")
    await bench.sync_vault()
    assert await bench.vault_paths() == {"personal/n.md"}
    note.unlink()

    await bench.sync_vault()

    assert await bench.vault_paths() == set()


async def test_a_vault_sync_never_drops_work_entries(bench: _Bench) -> None:
    secret = bench.vault / "work" / "s.md"
    secret.parent.mkdir(parents=True)
    secret.write_text("# S\nbody\n", encoding="utf-8")
    await bench.cache.set_entry("work/s.md", "work", "0" * 40, 1, repo=VAULT_REPO)
    await bench.store.upsert_batch(
        [
            VectorChunk(
                id="00000000-0000-0000-0000-000000000777",
                vector=[0.0] * VECTOR_SIZE,
                file_path="work/s.md",
                language="markdown",
                chunk_type="file",
                symbol="s",
                project=VAULT_REPO,
                ctx="work",
                content="restricted",
            )
        ]
    )
    (bench.vault / "personal").mkdir()
    (bench.vault / "personal" / "n.md").write_text("# N\nbody\n", encoding="utf-8")

    await bench.sync_vault()

    assert [p for p, _ in await bench.cache.list_entries("work", repo=VAULT_REPO)] == [
        "work/s.md"
    ]
    assert "work/s.md" in await bench.vault_paths()


def test_a_file_removed_while_the_dry_run_measures_the_tree_is_left_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "a.md").write_text("# A\nbody\n", encoding="utf-8")
    (repo / "b.md").write_text("# B\nbody\n", encoding="utf-8")
    real_iter = sync_module.iter_supported_files

    def list_then_remove(*args, **kwargs):
        files = list(real_iter(*args, **kwargs))
        (repo / "b.md").unlink()
        return files

    monkeypatch.setattr(sync_module, "iter_supported_files", list_then_remove)

    measured = sync_module.tree_size(repo, vault_root=tmp_path / "vault")

    assert measured == (1, len("# A\nbody\n"))


async def test_removing_the_last_file_empties_the_repo_in_the_index(bench: _Bench) -> None:
    await bench.sync()
    _git(bench.repo, "rm", "-q", "README.md")
    _git(bench.repo, "commit", "-m", "remove the last file")

    await bench.sync()

    assert await bench.indexed_paths() == set()


async def test_a_tree_restored_after_a_dirty_sync_is_indexed_again(bench: _Bench) -> None:
    (bench.repo / "README.md").write_text("# Title\nuncommitted\n", encoding="utf-8")
    await bench.sync()
    assert "uncommitted" in await bench.content_of("README.md")
    _git(bench.repo, "checkout", "--", "README.md")

    await bench.sync()

    content = await bench.content_of("README.md")
    assert "first" in content
    assert "uncommitted" not in content


async def test_first_sync_takes_the_ctx_of_the_legacy_rows(bench: _Bench) -> None:
    await bench.legacy_row("saas", "a.md")
    await bench.legacy_row("saas", "b.md")

    await bench.sync(ctx=None)

    state = await bench.cache.get_repo_state("myrepo")
    assert state is not None and state[0] == "saas"


async def test_a_later_sync_reuses_the_stored_ctx(bench: _Bench) -> None:
    await bench.sync(ctx="personal")
    _commit(bench.repo, "more.md", "# More\nbody\n")

    await bench.sync(ctx=None)

    state = await bench.cache.get_repo_state("myrepo")
    assert state is not None and state[0] == "personal"


async def test_first_sync_with_no_legacy_rows_asks_for_a_ctx(bench: _Bench) -> None:
    with pytest.raises(SyncCtxError, match="--ctx"):
        await bench.sync(ctx=None)
    assert await bench.indexed_paths() == set()


async def test_first_sync_with_disagreeing_legacy_rows_asks_for_a_ctx(
    bench: _Bench,
) -> None:
    await bench.legacy_row("saas", "a.md")
    await bench.legacy_row("personal", "b.md")

    with pytest.raises(SyncCtxError, match="--ctx"):
        await bench.sync(ctx=None)


async def test_work_is_never_chosen_implicitly(bench: _Bench) -> None:
    await bench.legacy_row("work", "a.md")

    with pytest.raises(SyncCtxError, match="work"):
        await bench.sync(ctx=None)
    assert await bench.indexed_paths() == set()
