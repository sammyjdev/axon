#!/usr/bin/env python3
"""Re-key sessions and session_memory rows holding absolute paths.

Dry run is the default. Applying changes requires an explicit scope flag:
  --sessions: re-key matching rows in the sessions table
  --session-memory: re-key matching rows in the session_memory table
  --all: re-key matching rows in both tables

Precedent and CLI shape follow axon rekey-repo (src/axon/__main__.py:392)
and scripts/purge_test_artifacts.py.
Rows are updated in place, never deleted.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import asyncpg

try:
    from scripts.pg_redact import redact_pg_url
except ImportError:
    from pg_redact import redact_pg_url

from axon.core.repo_identity import repo_identity

SELECT_SESSIONS_SQL = """
SELECT id, repo, started_at
FROM sessions
WHERE repo LIKE '/%'
ORDER BY started_at ASC
"""

UPDATE_SESSION_SQL = """
UPDATE sessions
SET repo = $1
WHERE id = $2
"""

SELECT_SESSION_MEMORY_SQL = """
SELECT id, project, created_at
FROM session_memory
WHERE project LIKE '/%'
ORDER BY created_at ASC
"""

UPDATE_SESSION_MEMORY_SQL = """
UPDATE session_memory
SET project = $1
WHERE id = $2
"""


def resolve_pg_url(pg_url_arg: str | None = None) -> str:
    """Resolve Postgres connection URL."""
    if pg_url_arg:
        return pg_url_arg
    env = os.environ.get("AXON_PG_URL")
    if env:
        return env
    try:
        from axon.config.runtime import load_runtime_config

        return load_runtime_config().pg_url
    except Exception:
        return "postgresql://axon:axon@localhost:5433/axon"


def resolve_key(path_str: str) -> str:
    """Resolve an absolute path key to a bare repo identity."""
    try:
        return repo_identity(Path(path_str).resolve())
    except Exception:
        return Path(path_str).name


async def inspect_sessions(pg_url: str) -> list[dict[str, str]]:
    """Fetch sessions rows with absolute path repo keys without modifying them."""
    try:
        con = await asyncpg.connect(pg_url)
    except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
        raise ConnectionError(
            f"Failed to connect to Postgres at {redact_pg_url(pg_url)}: {exc}"
        ) from exc
    try:
        rows = await con.fetch(SELECT_SESSIONS_SQL)
        return [
            {
                "id": str(r["id"]),
                "repo": str(r["repo"]),
                "new_repo": resolve_key(str(r["repo"])),
            }
            for r in rows
        ]
    except asyncpg.UndefinedTableError:
        return []
    finally:
        await con.close()


async def apply_rekey_sessions(pg_url: str) -> list[dict[str, str]]:
    """Update sessions rows with absolute path repo keys in place."""
    try:
        con = await asyncpg.connect(pg_url)
    except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
        raise ConnectionError(
            f"Failed to connect to Postgres at {redact_pg_url(pg_url)}: {exc}"
        ) from exc
    try:
        async with con.transaction():
            rows = await con.fetch(SELECT_SESSIONS_SQL)
            results: list[dict[str, str]] = []
            for r in rows:
                old_repo = str(r["repo"])
                new_repo = resolve_key(old_repo)
                row_id = str(r["id"])
                await con.execute(UPDATE_SESSION_SQL, new_repo, row_id)
                results.append(
                    {
                        "id": row_id,
                        "repo": old_repo,
                        "new_repo": new_repo,
                    }
                )
            return results
    except asyncpg.UndefinedTableError:
        return []
    finally:
        await con.close()


async def inspect_session_memory(pg_url: str) -> list[dict[str, str]]:
    """Fetch session_memory rows with absolute path project keys without modifying them."""
    try:
        con = await asyncpg.connect(pg_url)
    except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
        raise ConnectionError(
            f"Failed to connect to Postgres at {redact_pg_url(pg_url)}: {exc}"
        ) from exc
    try:
        rows = await con.fetch(SELECT_SESSION_MEMORY_SQL)
        return [
            {
                "id": str(r["id"]),
                "project": str(r["project"]),
                "new_project": resolve_key(str(r["project"])),
            }
            for r in rows
        ]
    except asyncpg.UndefinedTableError:
        return []
    finally:
        await con.close()


async def apply_rekey_session_memory(pg_url: str) -> list[dict[str, str]]:
    """Update session_memory rows with absolute path project keys in place."""
    try:
        con = await asyncpg.connect(pg_url)
    except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
        raise ConnectionError(
            f"Failed to connect to Postgres at {redact_pg_url(pg_url)}: {exc}"
        ) from exc
    try:
        async with con.transaction():
            rows = await con.fetch(SELECT_SESSION_MEMORY_SQL)
            results: list[dict[str, str]] = []
            for r in rows:
                old_project = str(r["project"])
                new_project = resolve_key(old_project)
                row_id = int(r["id"])
                await con.execute(UPDATE_SESSION_MEMORY_SQL, new_project, row_id)
                results.append(
                    {
                        "id": str(row_id),
                        "project": old_project,
                        "new_project": new_project,
                    }
                )
            return results
    except asyncpg.UndefinedTableError:
        return []
    finally:
        await con.close()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Re-key sessions and session_memory rows holding absolute paths.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Write changes (default: dry run)",
    )
    parser.add_argument(
        "--sessions",
        action="store_true",
        default=False,
        help="Scope to sessions table in Postgres",
    )
    parser.add_argument(
        "--session-memory",
        action="store_true",
        default=False,
        help="Scope to session_memory table in Postgres",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        default=False,
        help="Scope to both sessions and session_memory",
    )
    parser.add_argument(
        "--pg-url",
        type=str,
        default=None,
        help="Postgres URL (overrides AXON_PG_URL / runtime config)",
    )
    return parser.parse_args(argv)


async def run(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    if args.apply and not (args.sessions or args.session_memory or args.all):
        sys.stderr.write(
            "Refusing to re-key: pass --sessions, --session-memory, or --all with --apply.\n"
        )
        return 2

    if args.all and (args.sessions or args.session_memory):
        sys.stderr.write(
            "--all and --sessions/--session-memory are mutually exclusive: --all re-keys every "
            "target, while --sessions and --session-memory select specific tables.\n"
        )
        return 2

    if args.all:
        target_sessions = True
        target_memory = True
    elif args.sessions or args.session_memory:
        target_sessions = args.sessions
        target_memory = args.session_memory
    else:
        target_sessions = True
        target_memory = True

    pg_url = resolve_pg_url(args.pg_url)

    sessions: list[dict[str, str]] = []
    if target_sessions:
        try:
            if args.apply:
                sessions = await apply_rekey_sessions(pg_url)
            else:
                sessions = await inspect_sessions(pg_url)
        except (ConnectionError, OSError, asyncpg.PostgresConnectionError) as exc:
            sys.stderr.write(f"Error accessing sessions table: {exc}\n")
            return 1

    memory: list[dict[str, str]] = []
    if target_memory:
        try:
            if args.apply:
                memory = await apply_rekey_session_memory(pg_url)
            else:
                memory = await inspect_session_memory(pg_url)
        except (ConnectionError, OSError, asyncpg.PostgresConnectionError) as exc:
            sys.stderr.write(f"Error accessing session_memory table: {exc}\n")
            return 1

    action_verb = "re-keyed" if args.apply else "would re-key"

    if target_sessions:
        for row in sessions:
            print(f"{action_verb} session {row['id']}: {row['repo']} -> {row['new_repo']}")
        if not sessions:
            print("  (no matching session rows found)")

    if target_memory:
        for row in memory:
            print(
                f"{action_verb} session_memory {row['id']}: "
                f"{row['project']} -> {row['new_project']}"
            )
        if not memory:
            print("  (no matching session_memory rows found)")

    # Summary line whose wording differs between dry run and applied run
    if target_sessions and target_memory:
        if args.apply:
            print(f"re-keyed {len(sessions)} session(s) and {len(memory)} session_memory row(s)")
        else:
            print(
                f"would re-key {len(sessions)} session(s) and {len(memory)} session_memory row(s) "
                "(dry run; writing requires --apply with --sessions, --session-memory, or --all)"
            )
    elif target_sessions:
        if args.apply:
            print(f"re-keyed {len(sessions)} session(s)")
        else:
            print(
                f"would re-key {len(sessions)} session(s) "
                "(dry run; writing requires --apply with --sessions or --all)"
            )
    elif target_memory:
        if args.apply:
            print(f"re-keyed {len(memory)} session_memory row(s)")
        else:
            print(
                f"would re-key {len(memory)} session_memory row(s) "
                "(dry run; writing requires --apply with --session-memory or --all)"
            )

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
