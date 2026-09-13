#!/usr/bin/env python3
"""Re-key embeddings rows by deriving project from repo root.

Dry run is the default. Applying changes requires an explicit scope flag:
  --embeddings: re-key matching rows in the embeddings table
  --all: re-key all matching rows in the embeddings table

Precedent and CLI shape follow axon rekey-repo (src/axon/__main__.py:392)
and scripts/rekey_sessions.py.
Rows are updated in place, never deleted.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

import asyncpg

from axon.core.repo_identity import repo_identity

SELECT_EMBEDDINGS_SQL = """
SELECT id, file_path, project
FROM embeddings
ORDER BY file_path ASC, id ASC
"""

SELECT_EMBEDDINGS_FILTERED_SQL = """
SELECT id, file_path, project
FROM embeddings
WHERE project = $1
ORDER BY file_path ASC, id ASC
"""

UPDATE_EMBEDDINGS_SQL = """
UPDATE embeddings
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


def resolve_key(path_str: str, cache: dict[str, str] | None = None) -> str:
    """Resolve a file path to its repository identity, cached by directory."""
    p = Path(path_str)
    dir_path = p.parent
    dir_key = str(dir_path)
    if cache is not None and dir_key in cache:
        return cache[dir_key]
    try:
        new_proj = repo_identity(dir_path)
    except Exception:
        new_proj = dir_path.name
    if cache is not None:
        cache[dir_key] = new_proj
    return new_proj


async def inspect_embeddings(
    pg_url: str,
    only_project: str | None = None,
) -> list[dict[str, str]]:
    """Fetch embeddings rows that would change without modifying them."""
    try:
        con = await asyncpg.connect(pg_url)
    except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
        raise ConnectionError(f"Failed to connect to Postgres at {pg_url}: {exc}") from exc
    try:
        if only_project:
            rows = await con.fetch(SELECT_EMBEDDINGS_FILTERED_SQL, only_project)
        else:
            rows = await con.fetch(SELECT_EMBEDDINGS_SQL)
        cache: dict[str, str] = {}
        results: list[dict[str, str]] = []
        for r in rows:
            file_path = str(r["file_path"])
            old_project = str(r["project"]) if r["project"] is not None else ""
            new_project = resolve_key(file_path, cache=cache)
            if new_project != old_project:
                results.append(
                    {
                        "id": str(r["id"]),
                        "file_path": file_path,
                        "project": old_project,
                        "new_project": new_project,
                    }
                )
        return results
    except asyncpg.UndefinedTableError:
        return []
    finally:
        await con.close()


async def apply_rekey_embeddings(
    pg_url: str,
    only_project: str | None = None,
) -> list[dict[str, str]]:
    """Update embeddings rows in place."""
    try:
        con = await asyncpg.connect(pg_url)
    except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
        raise ConnectionError(f"Failed to connect to Postgres at {pg_url}: {exc}") from exc
    try:
        async with con.transaction():
            if only_project:
                rows = await con.fetch(SELECT_EMBEDDINGS_FILTERED_SQL, only_project)
            else:
                rows = await con.fetch(SELECT_EMBEDDINGS_SQL)
            cache: dict[str, str] = {}
            results: list[dict[str, str]] = []
            updates = []
            for r in rows:
                file_path = str(r["file_path"])
                old_project = str(r["project"]) if r["project"] is not None else ""
                new_project = resolve_key(file_path, cache=cache)
                if new_project != old_project:
                    row_id = str(r["id"])
                    updates.append((new_project, row_id))
                    results.append(
                        {
                            "id": row_id,
                            "file_path": file_path,
                            "project": old_project,
                            "new_project": new_project,
                        }
                    )
            if updates:
                await con.executemany(UPDATE_EMBEDDINGS_SQL, updates)
            return results
    except asyncpg.UndefinedTableError:
        return []
    finally:
        await con.close()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Re-key embeddings rows by deriving project from repo root.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Write changes (default: dry run)",
    )
    parser.add_argument(
        "--embeddings",
        action="store_true",
        default=False,
        help="Scope to embeddings table in Postgres",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        default=False,
        help="Scope to all matching rows in embeddings",
    )
    parser.add_argument(
        "--only-project",
        type=str,
        default=None,
        help="Filter candidate rows by current project",
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

    if args.apply and not (args.embeddings or args.all):
        sys.stderr.write(
            "Refusing to re-key: pass --embeddings or --all with --apply.\n"
        )
        return 2

    if args.all and args.embeddings:
        sys.stderr.write(
            "--all and --embeddings are mutually exclusive: --all re-keys every "
            "matching row, while --embeddings scopes to the embeddings table.\n"
        )
        return 2

    if args.all and args.only_project:
        sys.stderr.write(
            "--all and --only-project are mutually exclusive: --all re-keys every "
            "matching row, while --only-project selects a specific current project.\n"
        )
        return 2

    pg_url = resolve_pg_url(args.pg_url)

    try:
        if args.apply:
            changed = await apply_rekey_embeddings(pg_url, only_project=args.only_project)
        else:
            changed = await inspect_embeddings(pg_url, only_project=args.only_project)
    except (ConnectionError, OSError, asyncpg.PostgresConnectionError) as exc:
        sys.stderr.write(f"Error accessing embeddings table: {exc}\n")
        return 1

    action_verb = "re-keyed" if args.apply else "would re-key"

    for row in changed:
        print(f"{action_verb} embedding {row['id']}: {row['project']} -> {row['new_project']}")
    if not changed:
        print("  (no matching embedding rows found)")

    # Per-key counts
    for source, count in sorted(Counter(r["project"] for r in changed).items()):
        print(f"  {source}: {count} row(s)")

    # Summary line whose wording differs between dry run and applied run
    if args.apply:
        print(f"re-keyed {len(changed)} embedding row(s)")
    else:
        print(
            f"would re-key {len(changed)} embedding row(s) "
            "(dry run; writing requires --apply with --embeddings or --all)"
        )

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
