from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from axon.config.projects import ProjectEntry, load_project_manifest
from axon.embedder.engine import EmbedderEngine
from axon.embedder.pipeline import (
    excluded_path_patterns,
    index_path,
    infer_ctx_from_path,
    is_ctx_indexable,
    iter_supported_files,
)
from axon.store.pg_file_cache import PostgresFileCache
from axon.store.pg_vector_store import PgVectorStore

DEFAULT_DSN = "postgresql://axon:axon@localhost:5434/axon"
ABLATION_TABLE = "embeddings_ablation"
CACHE_NAMESPACE = "ablation"


@dataclass(frozen=True)
class RootPlan:
    name: str
    path: Path
    ctx: str | None
    languages: set[str] | None


class NamespacedFileCache:
    def __init__(self, inner, namespace: str) -> None:
        self._inner = inner
        self._namespace = namespace

    def _ctx(self, ctx: str) -> str:
        return f"{self._namespace}:{ctx}"

    async def get_all_sha1s(self, ctx: str) -> dict[str, str]:
        return await self._inner.get_all_sha1s(self._ctx(ctx))

    async def set_entry(
        self,
        file_path: str,
        ctx: str,
        sha1: str,
        chunk_count: int,
        *,
        status: str = "done",
    ) -> None:
        await self._inner.set_entry(
            file_path,
            self._ctx(ctx),
            sha1,
            chunk_count,
            status=status,
        )

    async def delete_entry(self, file_path: str, ctx: str) -> None:
        await self._inner.delete_entry(file_path, self._ctx(ctx))

    async def list_entries(self, ctx: str) -> list[tuple[str, str]]:
        return await self._inner.list_entries(self._ctx(ctx))


class NoCache:
    async def get_all_sha1s(self, ctx: str) -> dict[str, str]:
        return {}

    async def set_entry(
        self,
        file_path: str,
        ctx: str,
        sha1: str,
        chunk_count: int,
        *,
        status: str = "done",
    ) -> None:
        return None

    async def delete_entry(self, file_path: str, ctx: str) -> None:
        return None

    async def list_entries(self, ctx: str) -> list[tuple[str, str]]:
        return []


def build_roots(vault_root: Path, projects: list[ProjectEntry]) -> list[RootPlan]:
    roots = [RootPlan("vault", vault_root.expanduser(), None, None)]
    roots.extend(
        RootPlan(p.name, p.path.expanduser(), p.ctx, set(p.languages))
        for p in projects
        if p.enabled
    )
    return roots


def _files_for_plan(root: RootPlan, vault_root: Path) -> list[Path]:
    files = list(iter_supported_files(root.path, languages=root.languages))
    if root.ctx is not None:
        return files
    return [p for p in files if is_ctx_indexable(infer_ctx_from_path(p, vault_root), root.ctx)]


def _load_projects(manifest: Path) -> list[ProjectEntry]:
    return load_project_manifest(manifest.expanduser())


def dry_run(roots: list[RootPlan], vault_root: Path) -> None:
    print(f"table: {ABLATION_TABLE}")
    print(f"cache namespace: {CACHE_NAMESPACE}")
    print(f"exclusions: {', '.join(excluded_path_patterns()) or '(none)'}")
    for root in roots:
        files = _files_for_plan(root, vault_root)
        ctx = root.ctx or "auto"
        languages = ",".join(sorted(root.languages)) if root.languages else "all"
        print(f"{root.name}: ctx={ctx} languages={languages} files={len(files)} path={root.path}")


async def build_index(
    roots: list[RootPlan],
    *,
    dsn: str,
    vault_root: Path,
    no_cache: bool,
) -> None:
    engine = EmbedderEngine()
    store = PgVectorStore(dsn, table=ABLATION_TABLE)
    raw_cache = PostgresFileCache(dsn=dsn)
    file_cache = NoCache() if no_cache else NamespacedFileCache(raw_cache, CACHE_NAMESPACE)
    totals: list[tuple[str, int, int]] = []

    try:
        await store.ensure_collections()
        if not no_cache:
            await raw_cache.ensure_schema()

        for root in roots:
            indexed_files, chunks = await index_path(
                root.path,
                engine=engine,
                store=store,
                vault_root=vault_root,
                file_cache=file_cache,
                forced_ctx=root.ctx,
                languages=root.languages,
            )
            totals.append((root.name, indexed_files, chunks))
            print(f"{root.name}: {indexed_files} file(s), {chunks} chunk(s)")
    finally:
        await store.close()
        await raw_cache.close()

    total_files = sum(files for _, files, _ in totals)
    total_chunks = sum(chunks for _, _, chunks in totals)
    print(f"total: {total_files} file(s), {total_chunks} chunk(s)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the retrieval ablation index.")
    parser.add_argument("--dry-run", action="store_true", help="List roots and file counts only.")
    parser.add_argument("--no-cache", action="store_true", help="Bypass file_index cache.")
    parser.add_argument(
        "--dsn",
        default=os.environ.get("AXON_PG_URL", DEFAULT_DSN),
        help="Postgres DSN.",
    )
    parser.add_argument(
        "--vault",
        type=Path,
        default=Path(os.environ.get("AXON_VAULT", "~/vault")).expanduser(),
        help="Vault root.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "config" / "projects.json",
        help="Project manifest.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    vault_root = args.vault.expanduser()
    roots = build_roots(vault_root, _load_projects(args.manifest))
    if args.dry_run:
        dry_run(roots, vault_root)
        return 0
    asyncio.run(
        build_index(
            roots,
            dsn=args.dsn,
            vault_root=vault_root,
            no_cache=args.no_cache,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
