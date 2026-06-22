# scripts/migrate_decisions.py
"""One-shot copy of decisions + ADRs from SQLite to Postgres (idempotent)."""
from __future__ import annotations


async def copy_decisions(src_repo, dst_repo, *, adr_projects) -> tuple[int, int]:
    decisions = await src_repo.all_decisions()
    for d in decisions:
        await dst_repo.save_decision(d)
    n_adr = 0
    for project in adr_projects:
        for adr in await src_repo.get_adrs(project, limit=10_000):
            await dst_repo.save_adr_inner(adr)
            n_adr += 1
    return len(decisions), n_adr


async def _main() -> None:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from axon.config.runtime import load_runtime_config
    from axon.store.decision_repository import SqliteDecisionRepository
    from axon.store.pg_decision_repository import PostgresDecisionRepository
    from axon.store.session_store import SessionStore

    rt = load_runtime_config()
    session = SessionStore(db_path=rt.db_path)
    await session.init()
    src = SqliteDecisionRepository(session)
    dst = PostgresDecisionRepository(rt.pg_url)
    try:
        await dst.ensure_schema()
        # derive the set of ADR projects from the decisions' repos
        projects = sorted({d.repo for d in await src.all_decisions()})
        n_dec, n_adr = await copy_decisions(src, dst, adr_projects=projects)
        print(f"copied {n_dec} decisions, {n_adr} adrs -> postgres")
    finally:
        await session.close()
        await dst.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(_main())
