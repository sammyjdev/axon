> **dec-121 Phase 1 complete:** The Qdrant-to-pgvector migration finished.
> `scripts/migrate_bluegreen.py`, `scripts/verify_migration.py`, and
> `tests/scripts/test_verify_migration_parity.py` were removed (obsolete).
> The sections below are kept as a historical record.

# Blue/Green Reindex Migration Runbook

One-shot migration to reindex existing Qdrant data with the Plan C code
(stable chunk-ids, chunk-cap splitting, per-file reconcile). Run manually on
the machine that holds the live Qdrant - never in automated tests.

## Why blue/green

Plan A changed chunk boundaries (80-line cap) and chunk-ids (occurrence-index
instead of start-line). A plain reindex into the live collection mixes old and
new points until reconcile catches up. Blue/green builds the new index in a
side collection, validates recall, then swaps - with the old collection kept
for rollback.

## Preconditions

- Plan C code deployed (this branch).
- GPU embedding available (see `docs/gpu-setup.md`); `_detect_providers()`
  returns `CUDAExecutionProvider` first.
- Recall baseline committed at `tests/recall/baseline.json`.

## Steps

1. Preview, then create the side collections:

   ```
   python scripts/migrate_bluegreen.py --dry-run
   python scripts/migrate_bluegreen.py
   ```

2. Reindex into the new collection. Point indexing at `<ctx>_new` and run a
   full index of the vault for that ctx. Confirm the embedding model did not
   fall back to CPU (see gpu-setup.md bound-provider check).

3. Verify no orphans:

   ```
   python scripts/verify_migration.py --ctx personal_new
   ```

4. Run the recall gate (must be no regression vs the committed baseline):

   ```
   AXON_RUN_RECALL=1 python -m pytest tests/recall/test_recall_guard.py
   ```

5. Swap. Qdrant will not let an alias reuse a name still held by a collection,
   so:
   - Snapshot the old `personal` collection (Qdrant snapshot API) for rollback.
   - Delete the old `personal` collection.
   - Create the alias: `python scripts/migrate_bluegreen.py --swap-aliases`
     (or `--swap-aliases --dry-run` first).

6. Smoke-test reads (recall query, `axon ask`), then once confident delete the
   snapshot/`personal_old` backup.

## Rollback

If recall regresses or reads break after the swap: delete the `personal` alias,
restore the old collection from the snapshot, and re-point reads at it. The
`<ctx>_new` collection can be dropped and the migration retried.

---

# pgvector Cutover Runbook

This section covers switching the active vector backend from Qdrant to pgvector,
verifying parity, and rolling back if needed.

## Prerequisites

- Docker Compose with `axon-postgres` service defined (see `docker-compose.yml`).
- `AXON_PG_URL` env var pointing at the Postgres instance (default:
  `postgresql://axon:axon@localhost:5432/axon`).
- The vault is already indexed in Qdrant (existing data intact).

## Cutover Sequence

### 1. Start the Postgres backend

```bash
docker compose up -d axon-postgres
```

Wait for the service to be healthy before proceeding.

### 2. Re-index each ctx into pgvector (FULL re-index)

Re-index every ctx that actually holds vector data. Check which ones do first
(empty ctxs need nothing):

```bash
for c in knowledge personal career saas; do
  echo -n "$c: "; curl -s "http://localhost:6333/collections/$c" \
    | .venv/Scripts/python.exe -c "import sys,json;print(json.load(sys.stdin)['result']['points_count'])"
done
```

Do NOT blindly index restricted contexts (e.g. `work`); only migrate a
restricted ctx if it has data and you explicitly intend to.

CRITICAL - the indexer is INCREMENTAL. A plain `pb index` compares each file
against the SQLite file_cache (sha1) and SKIPS unchanged files, so pointing it
at a fresh pgvector backend writes NOTHING (the cache still says "done" from the
Qdrant era). Force a FULL re-index with a throwaway file_cache by pointing
`AXON_ENGINE` at a temp dir (this never touches your real `axon.db`):

```bash
TMP=$(mktemp -d); mkdir -p "$TMP/data"
AXON_ENGINE="$TMP" AXON_VAULT="<your-vault>" \
AXON_VECTOR_BACKEND=pgvector AXON_PG_URL="postgresql://axon:axon@localhost:5433/axon" \
PYTHONPATH=src .venv/Scripts/python.exe -m axon.cli.pb index --ctx knowledge
rm -rf "$TMP"
```

(The top-level `axon index` command was removed; use `pb index`. After the
flip, normal `pb index` runs against your real cache and stays consistent:
unchanged files are already in pgvector, changed files re-embed into it.)

### 3. Parity check (counts only - no model load)

```bash
.venv/Scripts/python.exe scripts/verify_migration.py --parity
```

The script prints per-ctx Qdrant vs pgvector counts. NOTE: exact count parity
assumes BOTH backends indexed the same current vault. If your Qdrant index is
older (a chunker change or vault edits since it was built), the counts will
differ even though pgvector is correct - this is Qdrant staleness, not a
pgvector defect. The authoritative quality gate is the recall gate (step 4); if
you want exact count parity, re-index Qdrant fresh first.

### 4. Recall gate (the authoritative gate)

```bash
AXON_VECTOR_BACKEND=pgvector AXON_RUN_RECALL=1 AXON_PG_URL="postgresql://axon:axon@localhost:5433/axon" \
  .venv/Scripts/python.exe -m pytest tests/recall/test_recall_guard.py::test_recall_guard_no_regression -q
```

The gate is REGRESSION-based, not an absolute threshold: it requires no
per-query rank regression and no Top-3 drop vs the committed
`tests/recall/baseline.json` (currently Top-1 ~0.55, Top-3 ~0.90). The pgvector
recall path runs against an ISOLATED `recall_embeddings` table (not the
production `embeddings` table), so running this gate after the flip never
touches or wipes your real vault vectors.

### 5. Flip the backend in axon.toml

Edit `axon.toml` (create it at `$AXON_ENGINE/axon.toml` if absent):

```toml
[runtime]
vector_backend = "pgvector"
```

### 6. Confirm doctor output

```bash
axon doctor
```

The output must contain:

```
vector_backend: pgvector
```

## Rollback

Revert `axon.toml` to restore Qdrant:

```toml
[runtime]
vector_backend = "qdrant"
```

Or override with the env var for a single command:

```bash
AXON_VECTOR_BACKEND=qdrant axon doctor
```

Qdrant data is intact throughout - no data is deleted by the cutover process.
Qdrant is retired only in the dec-121 step 5 cleanup (separate task, out of
scope here).

## Notes

- Plain hyphens only in all config values and paths - never em or en dashes.
- The `--parity` flag performs counts-only comparison; no model is loaded.
- A FAIL from parity means the pgvector index is incomplete - re-run indexing
  for the affected ctx before retrying.

# file_index Cutover Runbook (dec-121 step 3, wave 1)

The `file_index` (the incremental indexing cache) is selected by
`AXON_FILEINDEX_BACKEND` env > `axon.toml [runtime] fileindex_backend` >
default. As of this wave the default is `postgres`.

## Why no data copy

`file_index` is a CACHE (file -> sha1 -> done/pending), not a source of truth.
Switching backends leaves an empty Postgres table that the NEXT index rebuilds -
a one-time full re-index, the same incremental-cache behavior as the vector
cutover. There is nothing to migrate.

## Cutover sequence

```bash
docker compose up -d axon-postgres
# Full index (Postgres file_index starts empty -> every file is processed once):
AXON_FILEINDEX_BACKEND=postgres AXON_PG_URL="postgresql://axon:axon@localhost:5433/axon" \
  PYTHONPATH=src .venv/Scripts/python.exe -m axon.cli.pb index --ctx knowledge
# Verify populated:
docker compose exec -T axon-postgres psql -U axon -d axon -tAc "SELECT count(*) FROM file_index;"
# Second run MUST dedup (0 files, 0 chunks processed):
AXON_FILEINDEX_BACKEND=postgres AXON_PG_URL="..." PYTHONPATH=src \
  .venv/Scripts/python.exe -m axon.cli.pb index --ctx knowledge
```

Then set `fileindex_backend = "postgres"` in `axon.toml` (already the default).

## Rollback

Set `fileindex_backend = "sqlite"` (or `AXON_FILEINDEX_BACKEND=sqlite` for one
command). The SQLite `file_index` is untouched by the Postgres path; the next
sqlite index reconciles it against the repo. No data is lost.

## Mixed backend is expected during step 3

Only `file_index` moves this wave; the graph, decisions, and sessions stay on
SQLite until their waves. This is safe because `_open_file_cache` owns a
dedicated connection separate from the graph/decisions `SessionStore`.

# graph Cutover Runbook (dec-121 step 3, wave 2)

The code graph (nodes/edges) is selected by `AXON_GRAPH_BACKEND` env >
`axon.toml [runtime] graph_backend` > default. As of this wave the default is
`postgres`. `SessionStore` delegates its 7 graph methods to the configured
`GraphRepository`; GLYPH is unchanged (it builds a `NetworkXStore` from
`all_nodes`/`all_edges`, ADR Option A).

## Why a data copy (not a re-index)

Nodes are index-derived, but the `touches` edges come from git events (commit
history) and are NOT reproduced by a pure re-index. The copy preserves them.

## Cutover sequence

```bash
docker compose up -d axon-postgres
# Copy nodes/edges SQLite -> Postgres (idempotent):
PYTHONPATH=src AXON_PG_URL="postgresql://axon:axon@localhost:5433/axon" \
  .venv/Scripts/python.exe scripts/migrate_graph.py
# Count parity (must equal the SQLite nodes/edges counts):
docker compose exec -T axon-postgres psql -U axon -d axon -tAc \
  "SELECT (SELECT count(*) FROM nodes), (SELECT count(*) FROM edges);"
# GLYPH parity: a subgraph query returns the same neighborhood on both backends.
```

Then set `graph_backend = "postgres"` in `axon.toml` (already the default).

## Rollback

Set `graph_backend = "sqlite"` (or `AXON_GRAPH_BACKEND=sqlite` for one command).
The copy is one-way and non-destructive, so the SQLite graph is intact.

## Mixed backend is expected

Only the graph moves this wave; decisions and session continuity stay on SQLite
until their waves. SessionStore keeps a single aiosqlite connection for those
while the graph methods delegate to the Postgres repository.

# decisions/ADRs Cutover Runbook (dec-121 step 3, wave 3)

The decision knowledge base (`decisions` + `adr` tables) is selected by
`AXON_DECISIONS_BACKEND` env > `axon.toml [runtime] decisions_backend` > default.
As of this wave the default is `postgres`. `SessionStore` delegates its
decision/ADR methods to the configured `DecisionRepository`. On Postgres the
`decisions.frontmatter` is JSONB (GIN-indexed) and the find_* queries use native
operators; `judged`/`validation_score` live inside that JSON and round-trip as
real values (no separate column, no `validation_score == 0.0` sentinel).

## Cutover sequence

```bash
docker compose up -d axon-postgres
# Copy decisions + ADRs SQLite -> Postgres (idempotent):
PYTHONPATH=src AXON_PG_URL="postgresql://axon:axon@localhost:5433/axon" \
  .venv/Scripts/python.exe scripts/migrate_decisions.py
# Count parity:
docker compose exec -T axon-postgres psql -U axon -d axon -tAc \
  "SELECT (SELECT count(*) FROM decisions), (SELECT count(*) FROM adr);"
# Parity: find_decisions_by_repo / find_decision_by_git_hash return the same
# decisions under AXON_DECISIONS_BACKEND=postgres, and a judged=True decision
# keeps its judged value.
```

Then set `decisions_backend = "postgres"` in `axon.toml` (already the default).

## Rollback

Set `decisions_backend = "sqlite"` (or `AXON_DECISIONS_BACKEND=sqlite`). The copy
is one-way and non-destructive, so the SQLite decisions/adr tables are intact.

## Note on ADR idempotency

The Postgres ADR insert dedups on the exact (project, title, created_at) natural
key (so a copy re-run does not duplicate), whereas SQLite always inserts a new
row. Because `created_at` carries microsecond precision, distinct ADRs never
collide in normal use; the dedup only affects exact re-inserts (the migration).

# session continuity Cutover Runbook (dec-121 step 3, wave 4)

Session continuity (`session_memory` + `session_note` + `code_change` +
`sessions` tables) is selected by `AXON_SESSIONS_BACKEND` env >
`axon.toml [runtime] sessions_backend` > default. As of this wave the default is
`postgres`. `SessionStore` delegates its 9 session methods to the configured
`SessionRepository` (`SqliteSessionRepository` / `PostgresSessionRepository`).
On Postgres the rows use plain columns; memory/note inserts return the new id
via `RETURNING id`, `code_change` upserts on (commit_hash, file_path), and
`sessions` upserts on id. The SQLite-only db-locked pending fallback in
`save_code_change` has no Postgres equivalent (Postgres has no single-writer
lock), so the Postgres `save_code_change` == `save_code_change_inner`.

## Cutover sequence

```bash
docker compose up -d axon-postgres
# Copy memories/notes/code_changes/sessions SQLite -> Postgres (idempotent):
AXON_ENGINE="D:\axon" AXON_PG_URL="postgresql://axon:axon@localhost:5433/axon" \
  PYTHONPATH=src .venv/Scripts/python.exe scripts/migrate_sessions.py
# Count parity (order: session_note, sessions, code_change, session_memory):
docker compose exec -T axon-postgres psql -U axon -d axon -tAc \
  "SELECT (SELECT count(*) FROM session_note),(SELECT count(*) FROM sessions),(SELECT count(*) FROM code_change),(SELECT count(*) FROM session_memory);"
# Parity: get_recent_changes / get_session_memories return the same rows under
# AXON_SESSIONS_BACKEND=postgres.
```

Then set `sessions_backend = "postgres"` in `axon.toml` (already the default).

## Rollback

Set `sessions_backend = "sqlite"` (or `AXON_SESSIONS_BACKEND=sqlite` for one
command). The copy is one-way and non-destructive, so the SQLite session tables
are intact.

## Note on session payload re-wrap

`save_session` re-wraps `context_payload` as `{"recall": ...}`, so the copy
re-saves sessions with an empty advisory payload rather than the verbatim
original JSON. Session ids and lifecycle (agent/repo/ended_at) are preserved;
the payload is advisory and the live `sessions` data is tiny, so this is
acceptable. If exact payload fidelity is ever required, add a `save_session_raw`
that writes the row verbatim.

## This closes dec-121 step 3

All four per-concern backends (file_index, graph, decisions, sessions) now
default to `postgres`. The `AXON_DB_BACKEND` master switch (Task 7) lets a single
env var flip all four at once, with per-concern flags still taking precedence.
