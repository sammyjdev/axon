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
