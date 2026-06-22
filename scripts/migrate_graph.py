"""One-shot copy of the code graph (nodes/edges) from SQLite to Postgres.

Idempotent: add_node upserts, add_edge ignores duplicates. Preserves the
git-derived 'touches' edges that a re-index would not reproduce.
"""
from __future__ import annotations


async def copy_graph(src_repo, dst_repo) -> tuple[int, int]:
    nodes = await src_repo.all_nodes()
    for n in nodes:
        await dst_repo.add_node(
            n["id"], n["type"], label=n.get("label", ""), payload=n.get("payload") or {}
        )
    edges = await src_repo.all_edges()
    for e in edges:
        await dst_repo.add_edge(e)
    return len(nodes), len(edges)


async def _main() -> None:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from axon.config.runtime import load_runtime_config
    from axon.store.graph_repository import SqliteGraphRepository
    from axon.store.pg_graph_repository import PostgresGraphRepository
    from axon.store.session_store import SessionStore

    rt = load_runtime_config()
    session = SessionStore(db_path=rt.db_path)
    await session.init()
    src = SqliteGraphRepository(session)
    dst = PostgresGraphRepository(rt.pg_url)
    try:
        await dst.ensure_schema()
        n, e = await copy_graph(src, dst)
        print(f"copied {n} nodes, {e} edges -> postgres")
    finally:
        await session.close()
        await dst.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(_main())
