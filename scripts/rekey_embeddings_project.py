#!/usr/bin/env python3
"""Re-key embeddings rows by deriving project from repo root.

Dry run is the default. Applying changes requires an explicit scope flag:
  --embeddings: re-key matching rows in the embeddings table
  --all: re-key all matching rows in the embeddings table

Filter by project with --only-project <name>. Pass '' to select rows where
project is the empty string, and '<null>' to select rows where project IS NULL.

Precedent and CLI shape follow axon rekey-repo (src/axon/__main__.py:392)
and scripts/rekey_sessions.py.
Rows are updated in place, never deleted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg

try:
    from scripts.pg_redact import redact_pg_url
except ImportError:
    from pg_redact import redact_pg_url

try:
    from scripts.check_onboarding_drift import parse_canonical_repos
except ImportError:
    from check_onboarding_drift import parse_canonical_repos

from axon.core.repo_identity import RepoKeyResolution, resolve_repo_key

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

SELECT_EMBEDDINGS_NULL_PROJECT_SQL = """
SELECT id, file_path, project
FROM embeddings
WHERE project IS NULL
ORDER BY file_path ASC, id ASC
"""

UPDATE_EMBEDDINGS_SQL = """
UPDATE embeddings
SET project = $1
WHERE id = $2
"""


@dataclass(frozen=True, slots=True)
class ResolverInputs:
    known_names: frozenset[str]
    aliases: Mapping[str, str]
    vault_root: Path


@dataclass(frozen=True, slots=True)
class PlannedRow:
    id: str
    file_path: str
    project: str  # stored value, "" for NULL
    new_project: str  # resolved key

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "file_path": self.file_path,
            "project": self.project,
            "new_project": self.new_project,
        }


@dataclass(frozen=True, slots=True)
class RefusedRow:
    id: str
    file_path: str
    reason: str


@dataclass(frozen=True, slots=True)
class RekeyPlan:
    changed: list[PlannedRow]
    unchanged: list[PlannedRow]
    refused: list[RefusedRow]

    @property
    def total(self) -> int:
        return len(self.changed) + len(self.unchanged) + len(self.refused)


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


def plan_rekey(
    rows: Sequence[Any],
    *,
    inputs: ResolverInputs,
) -> RekeyPlan:
    """Plan re-keying across rows, returning changed, unchanged, and refused."""
    cache: dict[str, RepoKeyResolution] = {}
    changed: list[PlannedRow] = []
    unchanged: list[PlannedRow] = []
    refused: list[RefusedRow] = []

    for r in rows:
        row_id = str(r[0]) if isinstance(r, (tuple, list)) else str(r["id"])
        file_path = str(r[1]) if isinstance(r, (tuple, list)) else str(r["file_path"])
        project_val = r[2] if isinstance(r, (tuple, list)) else r["project"]
        old_project = str(project_val) if project_val is not None else ""

        p = Path(file_path)
        dir_path = p.parent
        dir_key = str(dir_path)
        if dir_key in cache:
            resolution = cache[dir_key]
        else:
            resolution = resolve_repo_key(
                dir_path,
                known_names=inputs.known_names,
                aliases=inputs.aliases,
                vault_root=inputs.vault_root,
            )
            cache[dir_key] = resolution

        if resolution.key is None:
            refused.append(
                RefusedRow(
                    id=row_id,
                    file_path=file_path,
                    reason=resolution.reason or "",
                )
            )
        elif resolution.key == old_project:
            unchanged.append(
                PlannedRow(
                    id=row_id,
                    file_path=file_path,
                    project=old_project,
                    new_project=resolution.key,
                )
            )
        else:
            changed.append(
                PlannedRow(
                    id=row_id,
                    file_path=file_path,
                    project=old_project,
                    new_project=resolution.key,
                )
            )

    return RekeyPlan(changed=changed, unchanged=unchanged, refused=refused)


def read_router_text() -> str | None:
    """Read ROUTER.md text from environment or default location."""
    path_str = os.environ.get("AXON_ROUTER_MD")
    router_path = Path(path_str) if path_str else (Path.home() / ".claude" / "axon" / "ROUTER.md")
    if not router_path.exists():
        return None
    try:
        return router_path.read_text(encoding="utf-8")
    except OSError:
        return None


def load_known_names(router_text: str | None) -> frozenset[str]:
    """Parse canonical repo names from router text, adding linkedin-content-manager and revvo."""
    if not router_text:
        return frozenset()
    parsed = parse_canonical_repos(router_text)
    if not parsed:
        return frozenset()
    return frozenset(parsed | {"linkedin-content-manager", "revvo"})


def load_aliases(manifest_path: Path | None = None) -> dict[str, str]:
    """Load repo aliases from config/projects.json or degrade to empty dict."""
    p = manifest_path or (Path(__file__).resolve().parent.parent / "config" / "projects.json")
    if not p.exists():
        sys.stderr.write(f"Warning: projects.json not found at {p}; using empty aliases\n")
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        aliases: dict[str, str] = {}
        for proj in data.get("projects", []):
            name = proj.get("name")
            path_str = proj.get("path")
            if name and path_str:
                aliases[name] = Path(path_str).name
        return aliases
    except Exception as exc:
        sys.stderr.write(f"Warning: failed to read {p}: {exc}; using empty aliases\n")
        return {}


def resolve_vault_root() -> Path:
    """Resolve vault root to an absolute path from AXON_VAULT or ~/vault."""
    env = os.environ.get("AXON_VAULT")
    raw = env if env else "~/vault"
    return Path(os.path.realpath(os.path.expanduser(raw)))


def build_resolver_inputs(manifest_path: Path | None = None) -> ResolverInputs:
    """Assemble all inputs for repo key resolution."""
    router_text = read_router_text()
    known = load_known_names(router_text)
    aliases = load_aliases(manifest_path)
    vault_root = resolve_vault_root()
    return ResolverInputs(known_names=known, aliases=aliases, vault_root=vault_root)


def known_names_gate(rows: Sequence[Any], known_names: frozenset[str]) -> str | None:
    """Check that known_names is non-empty if any dead directory rows exist."""
    if not known_names:
        for r in rows:
            file_path = str(r[1]) if isinstance(r, (tuple, list)) else str(r["file_path"])
            if not Path(file_path).parent.is_dir():
                return (
                    "Error: known repository names set is empty (missing or empty ROUTER.md) "
                    "but dead directories are present"
                )
    return None


def write_refused(plan: RekeyPlan, out_path: Path) -> None:
    """Write refused rows to a TSV file as id<TAB>file_path<TAB>reason."""
    lines: list[str] = []
    for r in plan.refused:
        clean_id = r.id.replace("\t", " ").replace("\n", " ")
        clean_path = r.file_path.replace("\t", " ").replace("\n", " ")
        clean_reason = r.reason.replace("\t", " ").replace("\n", " ")
        lines.append(f"{clean_id}\t{clean_path}\t{clean_reason}\n")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("".join(lines), encoding="utf-8")


def format_plan_counts(plan: RekeyPlan) -> list[str]:
    """Format plan counts for output."""
    return [
        f"changed {len(plan.changed)}",
        f"unchanged {len(plan.unchanged)}",
        f"refused {len(plan.refused)}",
        f"total {plan.total}",
    ]


async def fetch_rows_from_con(
    con: asyncpg.Connection,
    only_project: str | None = None,
) -> list[Any]:
    """Fetch rows from database using an active connection."""
    if only_project is not None:
        if only_project == "<null>":
            return await con.fetch(SELECT_EMBEDDINGS_NULL_PROJECT_SQL)
        return await con.fetch(SELECT_EMBEDDINGS_FILTERED_SQL, only_project)
    return await con.fetch(SELECT_EMBEDDINGS_SQL)


async def fetch_rows(
    pg_url: str,
    only_project: str | None = None,
) -> list[Any]:
    """Fetch rows from database, connecting and closing cleanly."""
    try:
        con = await asyncpg.connect(pg_url)
    except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
        raise ConnectionError(
            f"Failed to connect to Postgres at {redact_pg_url(pg_url)}: {exc}"
        ) from exc
    try:
        return await fetch_rows_from_con(con, only_project=only_project)
    finally:
        await con.close()


async def inspect_plan(
    pg_url: str,
    only_project: str | None = None,
    inputs: ResolverInputs | None = None,
) -> RekeyPlan:
    """Fetch embeddings rows and build a re-key plan without writing."""
    try:
        con = await asyncpg.connect(pg_url)
    except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
        raise ConnectionError(
            f"Failed to connect to Postgres at {redact_pg_url(pg_url)}: {exc}"
        ) from exc
    try:
        rows = await fetch_rows_from_con(con, only_project=only_project)
        resolver_inputs = inputs if inputs is not None else build_resolver_inputs()
        return plan_rekey(rows, inputs=resolver_inputs)
    finally:
        await con.close()


async def apply_plan(
    pg_url: str,
    only_project: str | None = None,
    inputs: ResolverInputs | None = None,
) -> RekeyPlan:
    """Update embeddings rows in place and return the plan."""
    try:
        con = await asyncpg.connect(pg_url)
    except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
        raise ConnectionError(
            f"Failed to connect to Postgres at {redact_pg_url(pg_url)}: {exc}"
        ) from exc
    try:
        async with con.transaction():
            rows = await fetch_rows_from_con(con, only_project=only_project)
            resolver_inputs = inputs if inputs is not None else build_resolver_inputs()
            plan = plan_rekey(rows, inputs=resolver_inputs)
            updates = [(row.new_project, row.id) for row in plan.changed]
            if updates:
                await con.executemany(UPDATE_EMBEDDINGS_SQL, updates)
            return plan
    finally:
        await con.close()


async def inspect_embeddings(
    pg_url: str,
    only_project: str | None = None,
) -> list[dict[str, str]]:
    """Fetch embeddings rows that would change without modifying them."""
    plan = await inspect_plan(pg_url, only_project=only_project)
    return [row.as_dict() for row in plan.changed]


async def apply_rekey_embeddings(
    pg_url: str,
    only_project: str | None = None,
) -> list[dict[str, str]]:
    """Update embeddings rows in place."""
    plan = await apply_plan(pg_url, only_project=only_project)
    return [row.as_dict() for row in plan.changed]


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
        help="Filter candidate rows by current project (pass '' for empty, '<null>' for NULL)",
    )
    parser.add_argument(
        "--pg-url",
        type=str,
        default=None,
        help="Postgres URL (overrides AXON_PG_URL / runtime config)",
    )
    parser.add_argument(
        "--refused-out",
        type=str,
        default=None,
        help="Write refused rows to TSV file (id, file_path, reason)",
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

    if args.all and args.only_project is not None:
        sys.stderr.write(
            "--all and --only-project are mutually exclusive: --all re-keys every "
            "matching row, while --only-project selects a specific current project.\n"
        )
        return 2

    pg_url = resolve_pg_url(args.pg_url)

    try:
        rows = await fetch_rows(pg_url, only_project=args.only_project)
    except (
        ConnectionError, OSError,
        asyncpg.PostgresConnectionError, asyncpg.UndefinedTableError,
        asyncpg.PostgresError, asyncpg.InterfaceError,
    ) as exc:
        sys.stderr.write(f"Error accessing embeddings table: {exc}\n")
        return 1

    resolver_inputs = build_resolver_inputs()
    gate_err = known_names_gate(rows, resolver_inputs.known_names)
    if gate_err:
        sys.stderr.write(f"{gate_err}\n")
        return 2

    if args.apply:
        try:
            plan = await apply_plan(pg_url, only_project=args.only_project, inputs=resolver_inputs)
        except (
            ConnectionError, OSError,
            asyncpg.PostgresConnectionError, asyncpg.UndefinedTableError,
            asyncpg.PostgresError, asyncpg.InterfaceError,
        ) as exc:
            sys.stderr.write(f"Error accessing embeddings table: {exc}\n")
            return 1
    else:
        plan = plan_rekey(rows, inputs=resolver_inputs)

    if args.refused_out:
        write_refused(plan, Path(args.refused_out))

    action_verb = "re-keyed" if args.apply else "would re-key"

    for row in plan.changed:
        print(f"{action_verb} embedding {row.id}: {row.project} -> {row.new_project}")
    if not plan.changed:
        print("  (no matching embedding rows found)")

    # Per-key counts
    for source, count in sorted(Counter(r.project for r in plan.changed).items()):
        print(f"  {source}: {count} row(s)")

    # Summary line whose wording differs between dry run and applied run
    if args.apply:
        print(f"re-keyed {len(plan.changed)} embedding row(s)")
    else:
        print(
            f"would re-key {len(plan.changed)} embedding row(s) "
            "(dry run; writing requires --apply with --embeddings or --all)"
        )

    for line in format_plan_counts(plan):
        print(line)

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
