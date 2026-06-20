#!/usr/bin/env python
"""Verify a migrated collection: scroll all points and flag orphan file_paths.

DO NOT RUN DURING AUTOMATED TESTS - it requires a live Qdrant. Run manually
after a blue/green migration:

  python scripts/verify_migration.py --ctx personal_new

An orphan is a point whose stored file_path no longer exists on disk - it
indicates the reconcile (delete-by-file) did not run for a removed/renamed
file. With Plan C's per-file reconcile a fresh reindex should produce zero
orphans.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from qdrant_client import QdrantClient

QDRANT_URL = "http://localhost:6333"


def scroll_all(client: QdrantClient, collection: str) -> list:
    all_points: list = []
    offset = None
    while True:
        result, next_offset = client.scroll(
            collection_name=collection,
            limit=1000,
            with_payload=True,
            offset=offset,
        )
        all_points.extend(result)
        if next_offset is None:
            break
        offset = next_offset
    return all_points


def main(ctx: str) -> None:
    client = QdrantClient(QDRANT_URL)
    points = scroll_all(client, ctx)
    print(f"Total points in '{ctx}': {len(points)}")

    file_paths = {p.payload.get("file_path") for p in points if p.payload}
    missing = sorted(fp for fp in file_paths if fp and not Path(fp).exists())

    print(f"Distinct file_paths: {len(file_paths)}")
    print(f"Orphan file_paths (file not on disk): {len(missing)}")
    for fp in missing:
        print(f"  ORPHAN: {fp}")

    if missing:
        print("\nWARNING: orphan points detected. Re-run `axon index` to reconcile.")
        raise SystemExit(1)
    print("\nOK: no orphan points detected.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scroll a collection and flag orphan file_paths")
    parser.add_argument("--ctx", default="personal", help="collection name to verify")
    main(parser.parse_args().ctx)
