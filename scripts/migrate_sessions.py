"""One-shot copy of session continuity (memories/notes/code_changes/sessions)
from SQLite to Postgres (idempotent)."""
from __future__ import annotations


async def copy_sessions(src_repo, dst_repo) -> dict:
    counts = {"memories": 0, "notes": 0, "code_changes": 0, "sessions": 0}
    for m in await src_repo.all_memories():
        await dst_repo.save_session_memory(m)
        counts["memories"] += 1
    for n in await src_repo.all_notes():
        await dst_repo.save_note(n)
        counts["notes"] += 1
    for c in await src_repo.all_code_changes():
        await dst_repo.save_code_change_inner(c)
        counts["code_changes"] += 1
    for s in await src_repo.all_sessions():
        # session re-save preserves id; payload is advisory (see plan note)
        await dst_repo.save_session(s["id"], s["agent"], s["repo"], context_payload="")
        counts["sessions"] += 1
    return counts


async def _main() -> None:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from axon.config.runtime import load_runtime_config
    from axon.store.pg_session_repository import PostgresSessionRepository
    from axon.store.session_repository import SqliteSessionRepository
    from axon.store.session_store import SessionStore

    rt = load_runtime_config()
    session = SessionStore(db_path=rt.db_path)
    await session.init()
    src = SqliteSessionRepository(session)
    dst = PostgresSessionRepository(rt.pg_url)
    try:
        await dst.ensure_schema()
        counts = await copy_sessions(src, dst)
        print(f"copied {counts} -> postgres")
    finally:
        await session.close()
        await dst.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(_main())
