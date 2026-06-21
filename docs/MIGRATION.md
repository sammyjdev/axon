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

### 2. Re-index each ctx into pgvector

Run the indexer against each context with the pgvector backend selected:

```bash
AXON_VECTOR_BACKEND=pgvector axon index <vault> --ctx personal
AXON_VECTOR_BACKEND=pgvector axon index <vault> --ctx work
```

Repeat for every ctx you maintain. Qdrant data is untouched.

### 3. Parity check (counts only - no model load)

```bash
python scripts/verify_migration.py --parity
```

The script prints per-ctx Qdrant vs pgvector counts and exits 0 on PASS or 1
on FAIL. All ctxs must show `OK` before proceeding.

### 4. Recall gate

```bash
AXON_VECTOR_BACKEND=pgvector AXON_RUN_RECALL=1 \
  .venv/Scripts/python.exe -m pytest tests/recall/test_recall_guard.py::test_recall_guard_no_regression -q
```

The recall guard must pass (Top-1 >= 0.60, Top-3 >= 0.90) before flipping the
default.

### 5. Flip the backend in axon.toml

Edit `axon.toml` (create it at `$AXON_ENGINE/axon.toml` if absent):

```toml
[runtime]
vector_backend = "pgvector"
```

### 6. Confirm doctor output

```bash
axon pb doctor
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
AXON_VECTOR_BACKEND=qdrant axon pb doctor
```

Qdrant data is intact throughout - no data is deleted by the cutover process.
Qdrant is retired only in the dec-121 step 5 cleanup (separate task, out of
scope here).

## Notes

- Plain hyphens only in all config values and paths - never em or en dashes.
- The `--parity` flag performs counts-only comparison; no model is loaded.
- A FAIL from parity means the pgvector index is incomplete - re-run indexing
  for the affected ctx before retrying.
