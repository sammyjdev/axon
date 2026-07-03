from __future__ import annotations

from pathlib import Path

import pytest


class _Cache:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def get_all_sha1s(self, ctx: str) -> dict[str, str]:
        self.calls.append(("get", ctx))
        return {"file.md": "sha1"}

    async def set_entry(self, file_path, ctx, sha1, chunk_count, *, status="done"):
        self.calls.append(("set", ctx))

    async def delete_entry(self, file_path, ctx):
        self.calls.append(("delete", ctx))

    async def list_entries(self, ctx):
        self.calls.append(("list", ctx))
        return [("file.md", "sha1")]


async def test_namespaced_cache_keeps_underlying_cache_separate() -> None:
    from scripts.build_ablation_index import NamespacedFileCache

    inner = _Cache()
    cache = NamespacedFileCache(inner, "ablation")

    assert await cache.get_all_sha1s("career") == {"file.md": "sha1"}
    await cache.set_entry("file.md", "career", "sha1", 1)
    await cache.delete_entry("file.md", "career")
    assert await cache.list_entries("career") == [("file.md", "sha1")]

    assert inner.calls == [
        ("get", "ablation:career"),
        ("set", "ablation:career"),
        ("delete", "ablation:career"),
        ("list", "ablation:career"),
    ]


def test_build_roots_uses_full_vault_and_enabled_dev_projects(tmp_path) -> None:
    from axon.config.projects import ProjectEntry
    from scripts.build_ablation_index import build_roots

    vault = tmp_path / "vault"
    dev = tmp_path / "dev"
    disabled = tmp_path / "disabled"

    roots = build_roots(
        vault,
        [
            ProjectEntry(
                name="dev",
                path=dev,
                ctx="personal",
                enabled=True,
                languages=("python", "markdown"),
            ),
            ProjectEntry(
                name="disabled",
                path=disabled,
                ctx="knowledge",
                enabled=False,
                languages=("markdown",),
            ),
        ],
    )

    assert [(r.name, r.path, r.ctx, r.languages) for r in roots] == [
        ("vault", vault, None, None),
        ("dev", dev, "personal", {"python", "markdown"}),
    ]


def test_dry_run_makes_no_db_or_embedder_calls(tmp_path, monkeypatch, capsys) -> None:
    from scripts import build_ablation_index as script

    def _boom(*_args, **_kwargs):
        raise AssertionError("dry_run must not construct DB/cache/embedder dependencies")

    monkeypatch.setattr(script, "PgVectorStore", _boom)
    monkeypatch.setattr(script, "PostgresFileCache", _boom)
    monkeypatch.setattr(script, "EmbedderEngine", _boom)

    root = tmp_path / "vault"
    root.mkdir()

    script.dry_run([script.RootPlan("vault", root, None, None)], root)

    out = capsys.readouterr().out
    assert "table: embeddings_ablation" in out
    assert "cache namespace: ablation" in out
    assert "vault: ctx=auto languages=all files=0" in out


@pytest.mark.asyncio
async def test_build_index_no_cache_uses_no_cache_and_never_ensures_real_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from scripts import build_ablation_index as script

    class _Engine:
        pass

    class _Store:
        async def ensure_collections(self) -> None:
            return None

        async def close(self) -> None:
            return None

    class _RawCache:
        async def ensure_schema(self) -> None:
            raise AssertionError("real cache schema must not be ensured with --no-cache")

        async def set_entry(self, *_args, **_kwargs) -> None:
            raise AssertionError("real cache must not be written with --no-cache")

        async def close(self) -> None:
            return None

    seen_cache_types: list[type] = []

    async def _fake_index_path(*_args, file_cache, **_kwargs):
        seen_cache_types.append(type(file_cache))
        return 0, 0

    monkeypatch.setattr(script, "EmbedderEngine", _Engine)
    monkeypatch.setattr(script, "PgVectorStore", lambda *_args, **_kwargs: _Store())
    monkeypatch.setattr(script, "PostgresFileCache", lambda *_args, **_kwargs: _RawCache())
    monkeypatch.setattr(script, "index_path", _fake_index_path)

    await script.build_index(
        [script.RootPlan("vault", tmp_path, None, None)],
        dsn="postgresql://example",
        vault_root=tmp_path,
        no_cache=True,
    )

    assert seen_cache_types == [script.NoCache]
