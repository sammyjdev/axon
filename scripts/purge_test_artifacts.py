#!/usr/bin/env python3
"""Purge fixture rows and test-written handoff briefs.

Dry run is the default. Applying changes requires an explicit scope flag:
  --decisions: purge matching decision rows in Postgres
  --briefs: purge matching test-written handoff briefs in Obsidian vault
  --all: purge both matching decisions and briefs

Precedent and CLI shape follow axon rekey-repo (src/axon/__main__.py:392).
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

FIXTURE_SUMMARIES: tuple[str, ...] = (
    "a decision",
    "first decision",
    "add redis cache",
    "drop neo4j backend",
    "adopt sqlite graph",
)

SELECT_DECISIONS_SQL = """
SELECT id, frontmatter->>'repo' AS repo,
       COALESCE(body, frontmatter->>'summary') AS summary, created_at
FROM decisions
WHERE body IN ($1, $2, $3, $4, $5)
   OR frontmatter->>'summary' IN ($1, $2, $3, $4, $5)
ORDER BY created_at ASC
"""

DELETE_DECISIONS_SQL = """
DELETE FROM decisions
WHERE body IN ($1, $2, $3, $4, $5)
   OR frontmatter->>'summary' IN ($1, $2, $3, $4, $5)
RETURNING id, frontmatter->>'repo' AS repo,
          COALESCE(body, frontmatter->>'summary') AS summary, created_at
"""


def is_test_brief(content: str) -> bool:
    """Return True if content represents a test-written handoff brief.

    Conjunctive selector:
    1. '## From this session' section is absent (no caller notes).
    2. Recalled context cites at least one of the known fixture summaries.
    """
    if "## From this session" in content:
        return False
    return any(summary in content for summary in FIXTURE_SUMMARIES)


def matching_summaries_in_brief(content: str) -> list[str]:
    """Return fixture summaries cited in a test-written brief, or empty list."""
    if "## From this session" in content:
        return []
    return [summary for summary in FIXTURE_SUMMARIES if summary in content]


def resolve_vault(vault_arg: Path | None = None) -> Path | None:
    """Resolve Obsidian vault root."""
    if vault_arg is not None:
        return vault_arg.expanduser().resolve()
    env = os.environ.get("AXON_VAULT")
    if env:
        return Path(env).expanduser().resolve()
    try:
        from axon.obsidian.discovery import discover_vault

        return discover_vault(use_cache=False)
    except Exception:
        return None


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


def scan_test_briefs(vault_path: Path | None) -> list[tuple[Path, list[str]]]:
    """Scan vault for test-written handoff briefs."""
    if vault_path is None:
        return []
    handoffs_dir = vault_path / "knowledge" / "handoffs"
    if not handoffs_dir.is_dir():
        return []

    matching: list[tuple[Path, list[str]]] = []
    for file_path in sorted(handoffs_dir.rglob("*.md")):
        if not file_path.is_file():
            continue
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        matches = matching_summaries_in_brief(content)
        if matches:
            matching.append((file_path, matches))
    return matching


async def inspect_decisions(pg_url: str) -> list[dict[str, object]]:
    """Fetch matching fixture decisions without modifying the store."""
    try:
        con = await asyncpg.connect(pg_url)
    except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
        raise ConnectionError(f"Failed to connect to Postgres at {pg_url}: {exc}") from exc
    try:
        rows = await con.fetch(SELECT_DECISIONS_SQL, *FIXTURE_SUMMARIES)
        return [
            {
                "id": str(r["id"]),
                "repo": str(r["repo"] or ""),
                "summary": str(r["summary"] or ""),
            }
            for r in rows
        ]
    except asyncpg.UndefinedTableError:
        return []
    finally:
        await con.close()


async def purge_decisions(pg_url: str) -> list[dict[str, object]]:
    """Delete matching fixture decisions from Postgres and return deleted rows."""
    try:
        con = await asyncpg.connect(pg_url)
    except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
        raise ConnectionError(f"Failed to connect to Postgres at {pg_url}: {exc}") from exc
    try:
        async with con.transaction():
            rows = await con.fetch(DELETE_DECISIONS_SQL, *FIXTURE_SUMMARIES)
            return [
                {
                    "id": str(r["id"]),
                    "repo": str(r["repo"] or ""),
                    "summary": str(r["summary"] or ""),
                }
                for r in rows
            ]
    except asyncpg.UndefinedTableError:
        return []
    finally:
        await con.close()


def purge_briefs(brief_paths: Sequence[Path]) -> None:
    """Delete matching test brief files."""
    for path in brief_paths:
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            pass


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Purge test fixture decision rows and test-written handoff briefs.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Write changes (default: dry run)",
    )
    parser.add_argument(
        "--decisions",
        action="store_true",
        default=False,
        help="Scope to decision rows in Postgres",
    )
    parser.add_argument(
        "--briefs",
        action="store_true",
        default=False,
        help="Scope to handoff briefs in Obsidian vault",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        default=False,
        help="Scope to both decisions and briefs",
    )
    parser.add_argument(
        "--vault",
        type=Path,
        default=None,
        help="Path to Obsidian vault (overrides AXON_VAULT / auto-discovery)",
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

    if args.apply and not (args.decisions or args.briefs or args.all):
        sys.stderr.write("Refusing to purge: pass --decisions, --briefs, or --all with --apply.\n")
        return 2

    if args.all and (args.decisions or args.briefs):
        sys.stderr.write(
            "--all and --decisions/--briefs are mutually exclusive: --all purges every "
            "target, while --decisions and --briefs select specific targets.\n"
        )
        return 2

    if args.all:
        target_decisions = True
        target_briefs = True
    elif args.decisions or args.briefs:
        target_decisions = args.decisions
        target_briefs = args.briefs
    else:
        target_decisions = True
        target_briefs = True

    decisions: list[dict[str, object]] = []
    if target_decisions:
        pg_url = resolve_pg_url(args.pg_url)
        try:
            if args.apply:
                decisions = await purge_decisions(pg_url)
            else:
                decisions = await inspect_decisions(pg_url)
        except (ConnectionError, OSError, asyncpg.PostgresConnectionError) as exc:
            sys.stderr.write(f"Error accessing decision store: {exc}\n")
            return 1

    briefs: list[tuple[Path, list[str]]] = []
    if target_briefs:
        vault_path = resolve_vault(args.vault)
        briefs = scan_test_briefs(vault_path)
        if args.apply:
            purge_briefs([path for path, _ in briefs])

    if target_decisions:
        print(
            "Notice: matching decisions by exact summary in:\n"
            + "".join(f"  - {s}\n" for s in FIXTURE_SUMMARIES)
            + "Any real decision sharing these exact summaries will be deleted because\n"
            + "exact match cannot distinguish it from a fixture."
        )
        action_verb = "deleted" if args.apply else "would delete"
        for row in decisions:
            print(f"{action_verb} decision {row['id']} ({row['repo']}): {row['summary']}")
        dec_counts = Counter(str(row["summary"]) for row in decisions)
        for summary, count in sorted(dec_counts.items()):
            print(f"  {summary}: {count} row(s)")
        if not decisions:
            print("  (no matching decision rows found)")

    if target_briefs:
        action_verb = "deleted" if args.apply else "would delete"
        for path, matches in briefs:
            print(f"{action_verb} brief {path.name} (cites: {', '.join(matches)})")
        brief_counts: Counter[str] = Counter()
        for _, matches in briefs:
            for summary in matches:
                brief_counts[summary] += 1
        for summary, count in sorted(brief_counts.items()):
            print(f"  {summary}: {count} brief(s)")
        if not briefs:
            print("  (no matching test briefs found)")

    # Summary line
    if target_decisions and target_briefs:
        if args.apply:
            print(f"purged {len(decisions)} decision(s) and {len(briefs)} brief(s)")
        else:
            print(
                f"would purge {len(decisions)} decision(s) and {len(briefs)} brief(s) "
                "(dry run; writing requires --apply with --decisions, --briefs, or --all)"
            )
    elif target_decisions:
        if args.apply:
            print(f"purged {len(decisions)} decision(s)")
        else:
            print(
                f"would purge {len(decisions)} decision(s) "
                "(dry run; writing requires --apply with --decisions or --all)"
            )
    elif target_briefs:
        if args.apply:
            print(f"purged {len(briefs)} brief(s)")
        else:
            print(
                f"would purge {len(briefs)} brief(s) "
                "(dry run; writing requires --apply with --briefs or --all)"
            )

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
