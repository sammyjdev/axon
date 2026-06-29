"""dec-121 backfill: copy legacy SQLite decisions/ADRs into Postgres, resolving
the decision-id collision. See docs/superpowers/specs/2026-06-29-dec121-decision-backfill-design.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class DecRef:
    id: str
    git_hash: str  # "" when absent
    content_key: str


@dataclass(frozen=True)
class BackfillPlan:
    copy_legacy: tuple[str, ...]
    renumber: tuple[tuple[str, str], ...]  # (old_pg_id, new_pg_id)
    skip_dup: tuple[str, ...]


def content_key(frontmatter: dict) -> str:
    """Canonical content key for a decision, excluding its id."""
    without_id = {k: v for k, v in frontmatter.items() if k != "id"}
    return json.dumps(without_id, sort_keys=True, ensure_ascii=False)


def _num(decision_id: str) -> int | None:
    if not decision_id.startswith("dec-"):
        return None
    try:
        return int(decision_id[4:])
    except ValueError:
        return None


def plan_backfill(sqlite: list[DecRef], pg: list[DecRef]) -> BackfillPlan:
    sqlite_ids = {d.id for d in sqlite}
    sqlite_git = {d.git_hash for d in sqlite if d.git_hash}
    sqlite_content = {d.content_key for d in sqlite}

    nums = [n for d in (*sqlite, *pg) if (n := _num(d.id)) is not None]
    next_num = (max(nums) if nums else 0) + 1

    renumber: list[tuple[str, str]] = []
    skip_dup: list[str] = []
    for d in pg:
        is_dup = (d.git_hash and d.git_hash in sqlite_git) or (
            not d.git_hash and d.content_key in sqlite_content
        )
        if is_dup:
            skip_dup.append(d.id)
            continue
        if d.id in sqlite_ids:  # native row colliding with a legacy id we will copy
            renumber.append((d.id, f"dec-{next_num:03d}"))
            next_num += 1
        # native + non-colliding id -> leave untouched
    return BackfillPlan(
        copy_legacy=tuple(d.id for d in sqlite),
        renumber=tuple(renumber),
        skip_dup=tuple(skip_dup),
    )


# --- executor ---
import sqlite3  # noqa: E402
from dataclasses import dataclass as _dataclass  # noqa: E402

import asyncpg  # noqa: E402


@_dataclass(frozen=True)
class BackfillReport:
    copied_decisions: int
    renumbered: tuple[tuple[str, str], ...]
    skipped_dup: tuple[str, ...]
    copied_adrs: int
    dry_run: bool


def _read_sqlite(sqlite_path: str):
    """Return (decision_rows, decref_list, adr_rows) from the legacy SQLite db."""
    con = sqlite3.connect(sqlite_path)
    con.row_factory = sqlite3.Row
    try:
        drows = con.execute(
            "SELECT id, frontmatter, body, vault_path, created_at FROM decisions"
        ).fetchall()
        arows = con.execute(
            "SELECT project, title, context, decision, rationale, created_at FROM adr"
        ).fetchall()
    finally:
        con.close()
    refs = []
    for r in drows:
        fm = json.loads(r["frontmatter"])
        refs.append(
            DecRef(id=r["id"], git_hash=fm.get("git_hash") or "", content_key=content_key(fm))
        )
    return drows, refs, arows


async def _read_pg_refs(con: asyncpg.Connection) -> list[DecRef]:
    rows = await con.fetch("SELECT id, frontmatter FROM decisions")
    refs = []
    for r in rows:
        fm = (
            json.loads(r["frontmatter"])
            if isinstance(r["frontmatter"], str)
            else dict(r["frontmatter"])
        )
        refs.append(
            DecRef(id=r["id"], git_hash=fm.get("git_hash") or "", content_key=content_key(fm))
        )
    return refs


async def run_backfill(sqlite_path: str, pg_dsn: str, *, dry_run: bool = False) -> BackfillReport:
    drows, sqlite_refs, arows = _read_sqlite(sqlite_path)
    con = await asyncpg.connect(pg_dsn)
    try:
        pg_refs = await _read_pg_refs(con)
        plan = plan_backfill(sqlite_refs, pg_refs)
        report = BackfillReport(
            copied_decisions=len(plan.copy_legacy),
            renumbered=plan.renumber,
            skipped_dup=plan.skip_dup,
            copied_adrs=len(arows),
            dry_run=dry_run,
        )
        if dry_run:
            return report

        async with con.transaction():
            # 1. renumber PG-native colliding rows (free the legacy ids)
            for old_id, new_id in plan.renumber:
                await con.execute(
                    "UPDATE decisions SET id=$1,"
                    " frontmatter=jsonb_set(frontmatter, '{id}', to_jsonb($1::text)) WHERE id=$2",
                    new_id, old_id,
                )
            # 2. drop duplicates (SQLite is authoritative)
            for dup_id in plan.skip_dup:
                await con.execute("DELETE FROM decisions WHERE id=$1", dup_id)
            # 3. copy legacy decisions verbatim
            for r in drows:
                await con.execute(
                    "INSERT INTO decisions (id, frontmatter, body, vault_path, created_at)"
                    " VALUES ($1, $2::jsonb, $3, $4, $5) ON CONFLICT (id) DO NOTHING",
                    r["id"], r["frontmatter"], r["body"], r["vault_path"], r["created_at"],
                )
            # 4. copy ADRs (PG assigns its own id; natural-key conflict is a no-op)
            for a in arows:
                await con.execute(
                    "INSERT INTO adr (project, title, context, decision, rationale, created_at)"
                    " VALUES ($1,$2,$3,$4,$5,$6)"
                    " ON CONFLICT (project, title, created_at) DO NOTHING",
                    a["project"], a["title"], a["context"], a["decision"], a["rationale"],
                    a["created_at"],
                )
        return report
    finally:
        await con.close()
