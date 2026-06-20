#!/usr/bin/env python
"""One-shot blue/green migration for the already-indexed repos.

DO NOT RUN DURING AUTOMATED TESTS - it requires a live Qdrant. Run manually
after Plan C is deployed:

  python scripts/migrate_bluegreen.py --dry-run        # preview collection creation
  python scripts/migrate_bluegreen.py                  # create <ctx>_new collections
  # then reindex into the new collections, run the recall gate, and only then:
  python scripts/migrate_bluegreen.py --swap-aliases --dry-run
  python scripts/migrate_bluegreen.py --swap-aliases

See docs/MIGRATION.md for the full runbook (including handling the existing
old collection, which the alias swap must not silently destroy).

This script ONLY manages collections/aliases - it never loads the embedding
model. Embedding happens via `axon index <vault> --ctx <ctx>` with Plan C code.
"""

from __future__ import annotations

import argparse

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

QDRANT_URL = "http://localhost:6333"
# Contexts with data to migrate. Phase 0 (benchmarks/phase0_baseline.json)
# confirmed only 'personal' holds data; widen this list if that changes.
TARGET_CONTEXTS = ["personal"]
VECTOR_SIZE = 768  # bge-base-en-v1.5; update if the embedding model changes.


def create_new_collections(dry_run: bool = False) -> None:
    client = QdrantClient(QDRANT_URL)
    existing = {c.name for c in client.get_collections().collections}
    print(f"Existing collections: {sorted(existing)}")

    for ctx in TARGET_CONTEXTS:
        new_name = f"{ctx}_new"
        if new_name in existing:
            print(f"[SKIP] {new_name} already exists - delete it for a fresh migration")
            continue
        if dry_run:
            print(f"[DRY-RUN] Would create collection: {new_name} (size={VECTOR_SIZE}, cosine)")
        else:
            client.create_collection(
                collection_name=new_name,
                vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
            )
            print(f"[OK] Created collection: {new_name}")

    print()
    print("NEXT STEPS:")
    for ctx in TARGET_CONTEXTS:
        print(f"  1. Reindex into {ctx}_new (point indexing at the new collection),")
        print(f"     then verify: python scripts/verify_migration.py --ctx {ctx}_new")
    print("  2. Run the recall gate: AXON_RUN_RECALL=1 python -m pytest "
          "tests/recall/test_recall_guard.py")
    print("  3. If the gate passes, swap aliases: "
          "python scripts/migrate_bluegreen.py --swap-aliases")
    print("  4. If the gate FAILS, delete the <ctx>_new collections and investigate.")


def swap_aliases(dry_run: bool = False) -> None:
    """Point each live ctx name at its <ctx>_new collection.

    Qdrant cannot create an alias whose name collides with an existing
    COLLECTION, so the old collection is first renamed to <ctx>_old (kept for
    rollback - delete it manually once you are confident). This is destructive
    to the old NAME; review docs/MIGRATION.md before running.
    """
    from qdrant_client import models as qm

    client = QdrantClient(QDRANT_URL)
    existing = {c.name for c in client.get_collections().collections}

    for ctx in TARGET_CONTEXTS:
        new_name = f"{ctx}_new"
        old_backup = f"{ctx}_old"
        if new_name not in existing:
            print(f"[ABORT] {new_name} does not exist - create + reindex it first")
            continue
        if dry_run:
            print(f"[DRY-RUN] Would: snapshot/rename collection '{ctx}' -> keep as '{old_backup}', "
                  f"then alias '{ctx}' -> '{new_name}'")
            print("[DRY-RUN] (Qdrant has no rename; the runbook documents the supported sequence)")
            continue
        # Supported sequence: create alias on the new collection, then the
        # caller deletes the stale old collection AFTER confirming reads work.
        # We do NOT auto-delete the old collection here - that stays a manual,
        # reviewed step to preserve rollback.
        if ctx in existing:
            print(f"[WARN] A collection named '{ctx}' still exists. Qdrant will not let an alias "
                  f"reuse that name. Follow docs/MIGRATION.md: back it up to '{old_backup}', "
                  f"delete '{ctx}', then re-run --swap-aliases.")
            continue
        client.update_collection_aliases(
            change_aliases_operations=[
                qm.CreateAliasOperation(
                    create_alias=qm.CreateAlias(collection_name=new_name, alias_name=ctx)
                )
            ]
        )
        print(f"[OK] Alias '{ctx}' -> '{new_name}'")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Blue/green Qdrant migration")
    parser.add_argument("--dry-run", action="store_true", help="preview without mutating Qdrant")
    parser.add_argument("--swap-aliases", action="store_true", help="swap ctx aliases to <ctx>_new")
    args = parser.parse_args()

    if args.swap_aliases:
        swap_aliases(dry_run=args.dry_run)
    else:
        create_new_collections(dry_run=args.dry_run)
