# dec-121 Decision/ADR Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An idempotent `pb migrate decisions-sqlite-to-pg` command that copies the 110 legacy SQLite decisions and 33 ADRs into the active Postgres store, renumbering the genuinely-new PG-native decisions whose ids collide with the legacy namespace.

**Architecture:** Three units. (1) A **pure planner** `plan_backfill(sqlite, pg)` that decides — with no DB access — which legacy ids to copy, which PG-native rows to renumber and to what, and which PG rows are duplicates to drop. (2) An **executor** `run_backfill(sqlite_path, pg_dsn, dry_run)` that reads both stores with raw SQL, calls the planner, and applies the result idempotently inside a transaction. (3) A thin **CLI** wrapper. The risky collision logic lives in the pure planner so it is exhaustively unit-tested without containers.

**Tech Stack:** Python 3.11+, `sqlite3` (stdlib, read legacy), `asyncpg` (write Postgres), Typer (`pb` CLI), pytest + `testcontainers.postgres`. No new dependencies.

## Global Constraints

- SQLite is **authoritative** for the legacy id namespace; PG-native rows whose id collides with a legacy id get **renumbered** (never the reverse).
- Renumber target = `max(numeric suffix across BOTH stores) + 1`, then increment per renumbered row. New ids are `dec-{n:03d}`.
- A PG row is a **duplicate** of a legacy row when its `git_hash` is non-empty and equals a SQLite row's `git_hash`, OR (empty git_hash) its content key equals a SQLite row's content key. Duplicates are dropped (SQLite wins), not renumbered.
- Decision ids match `^dec-\d{3,}$`; the numeric part is everything after the 4-char `dec-` prefix. Ignore any id not starting with `dec-` when computing the max.
- Copy is **verbatim row copy** (`id, frontmatter, body, vault_path, created_at`) — no domain re-serialization. PG `frontmatter` is `jsonb`; cast the SQLite text JSON with `::jsonb`.
- Renumber updates BOTH the row `id` and the `id` inside the `frontmatter` JSON (`jsonb_set`) so they never drift.
- Legacy decision copy uses `ON CONFLICT (id) DO NOTHING`; ADR copy uses `ON CONFLICT (project, title, created_at) DO NOTHING`. Both make re-runs no-ops.
- Read-only on SQLite. All writes target Postgres and are additive (insert + id reassign); no deletes against SQLite.
- `--dry-run` computes and reports the plan and writes nothing.
- Validation commands prefix with `rtk` (e.g. `rtk pytest tests/ -q`, `rtk ruff check`).

---

### Task 1: Pure collision planner

**Files:**
- Create: `src/axon/store/decision_backfill.py`
- Test: `tests/store/test_decision_backfill_planner.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) DecRef` with `id: str`, `git_hash: str` (`""` if absent), `content_key: str`.
  - `@dataclass(frozen=True) BackfillPlan` with `copy_legacy: tuple[str, ...]`, `renumber: tuple[tuple[str, str], ...]` (old_id, new_id), `skip_dup: tuple[str, ...]`.
  - `plan_backfill(sqlite: list[DecRef], pg: list[DecRef]) -> BackfillPlan`.
  - `content_key(frontmatter: dict) -> str` (canonical JSON of the frontmatter with `id` removed).

- [ ] **Step 1: Write the failing tests**

```python
# tests/store/test_decision_backfill_planner.py
from axon.store.decision_backfill import BackfillPlan, DecRef, content_key, plan_backfill


def _r(i, gh="", ck=""):
    return DecRef(id=i, git_hash=gh, content_key=ck or i)


def test_native_collision_is_renumbered_after_global_max():
    sqlite = [_r("dec-001", "h1"), _r("dec-002", "h2"), _r("dec-003", "h3")]
    pg = [_r("dec-001", "x1"), _r("dec-002", "x2")]  # collide by id, different git_hash
    plan = plan_backfill(sqlite, pg)
    assert plan.copy_legacy == ("dec-001", "dec-002", "dec-003")
    assert plan.renumber == (("dec-001", "dec-004"), ("dec-002", "dec-005"))
    assert plan.skip_dup == ()


def test_duplicate_by_git_hash_is_dropped_not_renumbered():
    sqlite = [_r("dec-001", "h1"), _r("dec-002", "h2")]
    pg = [_r("dec-001", "h2")]  # same git_hash as sqlite dec-002 -> duplicate
    plan = plan_backfill(sqlite, pg)
    assert plan.skip_dup == ("dec-001",)
    assert plan.renumber == ()


def test_noncolliding_pg_native_is_left_alone():
    sqlite = [_r("dec-001", "h1")]
    pg = [_r("dec-200", "x9")]  # native, no id collision -> no action
    plan = plan_backfill(sqlite, pg)
    assert plan.renumber == () and plan.skip_dup == ()
    assert plan.copy_legacy == ("dec-001",)


def test_empty_git_hash_duplicate_matched_by_content():
    sqlite = [_r("dec-001", "", ck="same-content")]
    pg = [_r("dec-001", "", ck="same-content")]  # empty git_hash, identical content
    plan = plan_backfill(sqlite, pg)
    assert plan.skip_dup == ("dec-001",) and plan.renumber == ()


def test_empty_git_hash_native_is_renumbered_when_content_differs():
    sqlite = [_r("dec-001", "", ck="legacy-content")]
    pg = [_r("dec-001", "", ck="new-content")]
    plan = plan_backfill(sqlite, pg)
    assert plan.renumber == (("dec-001", "dec-002"),) and plan.skip_dup == ()


def test_content_key_excludes_id():
    a = content_key({"id": "dec-001", "summary": "s", "repo": "r"})
    b = content_key({"id": "dec-999", "summary": "s", "repo": "r"})
    assert a == b  # id is excluded, rest identical
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `rtk pytest tests/store/test_decision_backfill_planner.py -v`
Expected: FAIL with `ModuleNotFoundError: axon.store.decision_backfill`

- [ ] **Step 3: Write the planner**

```python
# src/axon/store/decision_backfill.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `rtk pytest tests/store/test_decision_backfill_planner.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Lint + commit**

```bash
rtk ruff check src/axon/store/decision_backfill.py
git add src/axon/store/decision_backfill.py tests/store/test_decision_backfill_planner.py
git commit -m "feat(store): pure planner for dec-121 decision backfill collision resolution"
```

---

### Task 2: Backfill executor (SQLite read + Postgres apply)

**Files:**
- Modify: `src/axon/store/decision_backfill.py` (append the executor)
- Test: `tests/store/test_decision_backfill_executor.py`

**Interfaces:**
- Consumes: `DecRef`, `BackfillPlan`, `plan_backfill`, `content_key` (Task 1).
- Produces:
  - `@dataclass(frozen=True) BackfillReport` with `copied_decisions: int`, `renumbered: tuple[tuple[str, str], ...]`, `skipped_dup: tuple[str, ...]`, `copied_adrs: int`, `dry_run: bool`.
  - `async def run_backfill(sqlite_path: str, pg_dsn: str, *, dry_run: bool = False) -> BackfillReport`.

- [ ] **Step 1: Write the failing test**

```python
# tests/store/test_decision_backfill_executor.py
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

import pytest

pytest.importorskip("testcontainers.postgres")
import asyncpg  # noqa: E402
from testcontainers.postgres import PostgresContainer  # noqa: E402

from axon.store.decision_backfill import run_backfill  # noqa: E402
from axon.store.pg_decision_repository import PostgresDecisionRepository  # noqa: E402


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


def _fm(did, repo="axon", git_hash="", summary="s"):
    return {
        "id": did, "timestamp": "2026-01-01T00:00:00+00:00", "agent": "manual",
        "repo": repo, "files": [], "symbols": [], "summary": summary,
        "validation_score": 0.0, "judged": False, "git_hash": git_hash or None,
        "linked_decisions": [], "tags": [], "status": "draft",
    }


def _make_sqlite(path, decisions, adrs):
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE decisions (id TEXT PRIMARY KEY, frontmatter TEXT NOT NULL,"
        " body TEXT NOT NULL DEFAULT '', vault_path TEXT, created_at TEXT NOT NULL)"
    )
    con.execute(
        "CREATE TABLE adr (id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL,"
        " title TEXT NOT NULL, context TEXT NOT NULL, decision TEXT NOT NULL,"
        " rationale TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    for fm in decisions:
        con.execute(
            "INSERT INTO decisions (id, frontmatter, body, created_at) VALUES (?,?,?,?)",
            (fm["id"], json.dumps(fm), fm["summary"], fm["timestamp"]),
        )
    for a in adrs:
        con.execute(
            "INSERT INTO adr (project, title, context, decision, rationale, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (a["project"], a["title"], "ctx", "dec", "rat", a["created_at"]),
        )
    con.commit()
    con.close()


async def _pg_ids(dsn):
    con = await asyncpg.connect(dsn)
    try:
        rows = await con.fetch("SELECT id FROM decisions ORDER BY id")
        return [r["id"] for r in rows]
    finally:
        await con.close()


async def test_backfill_copies_legacy_and_renumbers_native(pg_dsn, tmp_path):
    # PG seeded with two native rows dec-001/dec-002 (different git hashes)
    repo = PostgresDecisionRepository(dsn=pg_dsn)
    await repo.ensure_schema()
    con = await asyncpg.connect(pg_dsn)
    try:
        await con.execute("DELETE FROM decisions")
        await con.execute("DELETE FROM adr")
        for did, gh in [("dec-001", "native-a"), ("dec-002", "native-b")]:
            await con.execute(
                "INSERT INTO decisions (id, frontmatter, body, created_at) VALUES ($1,$2::jsonb,$3,$4)",
                did, json.dumps(_fm(did, git_hash=gh)), "s", "2026-01-01T00:00:00+00:00",
            )
    finally:
        await con.close()

    sqlite_path = str(tmp_path / "legacy.db")
    _make_sqlite(
        sqlite_path,
        [_fm("dec-001", git_hash="h1"), _fm("dec-002", git_hash="h2"), _fm("dec-003", git_hash="h3")],
        [{"project": "axon", "title": "ADR-1", "created_at": "2026-01-01T00:00:00+00:00"}],
    )

    report = await run_backfill(sqlite_path, pg_dsn, dry_run=False)

    assert report.copied_decisions == 3
    assert report.renumbered == (("dec-001", "dec-004"), ("dec-002", "dec-005"))
    assert report.copied_adrs == 1
    ids = await _pg_ids(pg_dsn)
    assert ids == ["dec-001", "dec-002", "dec-003", "dec-004", "dec-005"]
    # the renumbered rows kept native content; legacy ids carry legacy git hashes
    con = await asyncpg.connect(pg_dsn)
    try:
        g1 = await con.fetchval("SELECT frontmatter->>'git_hash' FROM decisions WHERE id='dec-001'")
        g4 = await con.fetchval("SELECT frontmatter->>'git_hash' FROM decisions WHERE id='dec-004'")
        id4 = await con.fetchval("SELECT frontmatter->>'id' FROM decisions WHERE id='dec-004'")
    finally:
        await con.close()
    assert g1 == "h1" and g4 == "native-a" and id4 == "dec-004"


async def test_backfill_is_idempotent(pg_dsn, tmp_path):
    sqlite_path = str(tmp_path / "legacy2.db")
    _make_sqlite(sqlite_path, [_fm("dec-001", git_hash="h1")], [])
    con = await asyncpg.connect(pg_dsn)
    try:
        await con.execute("DELETE FROM decisions")
        await con.execute("DELETE FROM adr")
    finally:
        await con.close()

    first = await run_backfill(sqlite_path, pg_dsn, dry_run=False)
    before = await _pg_ids(pg_dsn)
    second = await run_backfill(sqlite_path, pg_dsn, dry_run=False)
    after = await _pg_ids(pg_dsn)
    assert before == after  # no new rows, no renumber churn
    assert second.renumbered == ()


async def test_dry_run_writes_nothing(pg_dsn, tmp_path):
    sqlite_path = str(tmp_path / "legacy3.db")
    _make_sqlite(sqlite_path, [_fm("dec-001", git_hash="h1")], [])
    con = await asyncpg.connect(pg_dsn)
    try:
        await con.execute("DELETE FROM decisions")
        await con.execute("DELETE FROM adr")
    finally:
        await con.close()
    report = await run_backfill(sqlite_path, pg_dsn, dry_run=True)
    assert report.dry_run is True and report.copied_decisions == 1
    assert await _pg_ids(pg_dsn) == []  # nothing written
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `rtk pytest tests/store/test_decision_backfill_executor.py -v`
Expected: FAIL with `ImportError: cannot import name 'run_backfill'`

- [ ] **Step 3: Append the executor to `decision_backfill.py`**

```python
# --- executor (append to src/axon/store/decision_backfill.py) ---
import sqlite3
from dataclasses import dataclass as _dataclass

import asyncpg


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
        refs.append(DecRef(id=r["id"], git_hash=fm.get("git_hash") or "", content_key=content_key(fm)))
    return drows, refs, arows


async def _read_pg_refs(con: asyncpg.Connection) -> list[DecRef]:
    rows = await con.fetch("SELECT id, frontmatter FROM decisions")
    refs = []
    for r in rows:
        fm = json.loads(r["frontmatter"]) if isinstance(r["frontmatter"], str) else dict(r["frontmatter"])
        refs.append(DecRef(id=r["id"], git_hash=fm.get("git_hash") or "", content_key=content_key(fm)))
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
```

Note: the `skip_dup` DELETE handles the rare duplicate case (a PG row that equals a legacy row). It runs before the legacy copy so the legacy id is free; the legacy copy then re-inserts the authoritative SQLite content.

- [ ] **Step 4: Run tests to verify they pass**

Run: `rtk pytest tests/store/test_decision_backfill_executor.py -v`
Expected: PASS (3 tests; the module spins its own Postgres container)

- [ ] **Step 5: Lint + commit**

```bash
rtk ruff check src/axon/store/decision_backfill.py
git add src/axon/store/decision_backfill.py tests/store/test_decision_backfill_executor.py
git commit -m "feat(store): dec-121 backfill executor (sqlite read + idempotent pg apply)"
```

---

### Task 3: `pb migrate decisions-sqlite-to-pg` CLI command

**Files:**
- Modify: `src/axon/cli/pb.py` (add a `migrate` sub-app + the command)
- Test: `tests/cli/test_migrate_cli.py`

**Interfaces:**
- Consumes: `run_backfill`, `BackfillReport` (Task 2); `_RUNTIME` (for default sqlite path + pg dsn).

- [ ] **Step 1: Write the failing test**

```python
# tests/cli/test_migrate_cli.py
from __future__ import annotations

from typer.testing import CliRunner

from axon.cli.pb import app


def test_migrate_decisions_dry_run_invokes_backfill(monkeypatch):
    captured = {}

    async def fake_run_backfill(sqlite_path, pg_dsn, *, dry_run=False):
        captured["args"] = (sqlite_path, pg_dsn, dry_run)
        from axon.store.decision_backfill import BackfillReport
        return BackfillReport(
            copied_decisions=110, renumbered=(("dec-001", "dec-111"),),
            skipped_dup=(), copied_adrs=33, dry_run=dry_run,
        )

    monkeypatch.setattr("axon.store.decision_backfill.run_backfill", fake_run_backfill)
    result = CliRunner().invoke(
        app, ["migrate", "decisions-sqlite-to-pg", "--dry-run", "--sqlite", "/tmp/x.db"]
    )
    assert result.exit_code == 0, result.output
    assert captured["args"][0] == "/tmp/x.db"
    assert captured["args"][2] is True  # dry_run threaded through
    assert "110" in result.output and "dec-001" in result.output and "dec-111" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk pytest tests/cli/test_migrate_cli.py -v`
Expected: FAIL (no `migrate` command registered on `app`)

- [ ] **Step 3: Add the `migrate` sub-app and command in `pb.py`**

Near the other sub-app definitions in `src/axon/cli/pb.py` (search for an existing `typer.Typer(` sub-app such as the `adr` app to match the registration pattern), add:

```python
migrate_app = typer.Typer(help="One-off data migrations (dec-121).")
app.add_typer(migrate_app, name="migrate")


@migrate_app.command("decisions-sqlite-to-pg")
def migrate_decisions_sqlite_to_pg(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the plan; write nothing.")
    ] = False,
    sqlite: Annotated[
        str | None, typer.Option("--sqlite", help="Legacy SQLite db path (default: runtime db_path).")
    ] = None,
) -> None:
    """Backfill legacy SQLite decisions + ADRs into the active Postgres store."""
    from axon.store import decision_backfill

    sqlite_path = sqlite or str(_RUNTIME.db_path)
    pg_dsn = _RUNTIME.pg_url
    if not pg_dsn:
        typer.echo("AXON_PG_URL / runtime pg_url is not set; nothing to migrate into.", err=True)
        raise typer.Exit(1)

    report = asyncio.run(decision_backfill.run_backfill(sqlite_path, pg_dsn, dry_run=dry_run))

    prefix = "[dry-run] " if report.dry_run else ""
    typer.echo(f"{prefix}decisions to copy: {report.copied_decisions}")
    typer.echo(f"{prefix}ADRs to copy: {report.copied_adrs}")
    for old_id, new_id in report.renumbered:
        typer.echo(f"{prefix}renumber {old_id} -> {new_id}")
    for dup in report.skipped_dup:
        typer.echo(f"{prefix}skip duplicate {dup}")
```

(Call `decision_backfill.run_backfill` via the module — not a direct `from … import run_backfill` — so the test's `monkeypatch.setattr("axon.store.decision_backfill.run_backfill", …)` is honored. `Annotated`, `typer`, `asyncio`, and `_RUNTIME` are already imported in `pb.py`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk pytest tests/cli/test_migrate_cli.py -v`
Expected: PASS

- [ ] **Step 5: Lint + commit**

```bash
rtk ruff check src/axon/cli/pb.py
git add src/axon/cli/pb.py tests/cli/test_migrate_cli.py
git commit -m "feat(cli): pb migrate decisions-sqlite-to-pg (dec-121 backfill wrapper)"
```

---

### Task 4: Dry-run on the real data, then apply (operational)

**Files:**
- None (operational validation against the live stores)

- [ ] **Step 1: Dry-run against the real SQLite + Postgres**

```bash
export AXON_PG_URL="postgresql://axon:axon@localhost:5434/axon"
pb migrate decisions-sqlite-to-pg --dry-run
```

Expected: `decisions to copy: 110`, `ADRs to copy: 33`, and renumber lines for the PG-native rows (e.g. `renumber dec-001 -> dec-111` … `dec-005 -> dec-115`). Confirm the renumber targets continue after the legacy max and that no legacy id is in the renumber's left column.

- [ ] **Step 2: Apply**

```bash
pb migrate decisions-sqlite-to-pg
```

- [ ] **Step 3: Verify in Postgres**

```bash
docker exec axon-axon-postgres-1 psql -U axon -d axon -c \
 "SELECT count(*) AS decisions FROM decisions;"   # expect 115
docker exec axon-axon-postgres-1 psql -U axon -d axon -c \
 "SELECT count(*) AS adrs FROM adr;"               # expect 33
docker exec axon-axon-postgres-1 psql -U axon -d axon -c \
 "SELECT json_extract_path_text(frontmatter,'repo') AS repo, count(*) FROM decisions GROUP BY 1 ORDER BY 2 DESC;"
```

Expect: 115 decisions (110 legacy + 5 renumbered native), 33 ADRs, and per-repo counts matching the legacy SQLite distribution (axon=57, PitStopOS=36, …). Re-run `pb migrate decisions-sqlite-to-pg` once more and confirm counts are unchanged (idempotent).

- [ ] **Step 4: Spot-check the unblocked path**

```bash
axon export adr --repo PitStopOS   # previously "No decisions"; now exports notes
```

Confirm `find_decisions_by_repo` now sees the backfilled repos. This re-confirms sub-project B Task 4 (vault re-export + re-index) is now unblocked.

---

## Self-Review

**Spec coverage:** command surface + `--dry-run` → Task 3; ADR copy → Task 2 step 3 (clause 4); decision copy + collision resolution → Task 1 (planner) + Task 2 (apply); idempotency → Task 2 (`ON CONFLICT DO NOTHING`, renumber-only-on-collision) with a dedicated test; dry-run → Task 2 + Task 3 tests; testing strategy → Tasks 1-2; operational apply → Task 4. Out-of-scope items (vault re-export/re-index, other tables, SQLite retirement) are intentionally untasked.

**Placeholder scan:** no TBD/TODO; every code step shows complete code. Task 4 is operational with exact commands and expected numbers (110/33/115).

**Type consistency:** `DecRef(id, git_hash, content_key)`, `BackfillPlan(copy_legacy, renumber, skip_dup)`, `BackfillReport(copied_decisions, renumbered, skipped_dup, copied_adrs, dry_run)`, `plan_backfill(list[DecRef], list[DecRef]) -> BackfillPlan`, `content_key(dict) -> str`, `run_backfill(str, str, *, dry_run) -> BackfillReport` are used consistently across tasks. Renumber tuple shape `(old_id, new_id)` matches between planner, executor, report, and CLI output.

**Known follow-ups (out of scope):** sub-project B Task 4 (re-export `AXON/*` + re-index); full dec-121 migration of the remaining stranded tables; an optional doctor guard for the "PG set but PG decisions ≪ SQLite" condition.
