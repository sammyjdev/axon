#!/usr/bin/env python
"""Verify a migrated collection: scroll all points and flag orphan file_paths.

DO NOT RUN DURING AUTOMATED TESTS - it requires a live Qdrant/pgvector. Run
manually after a blue/green migration:

  python scripts/verify_migration.py --ctx personal_new

An orphan is a point whose stored file_path no longer exists on disk - it
indicates the reconcile (delete-by-file) did not run for a removed/renamed
file. With Plan C's per-file reconcile a fresh reindex should produce zero
orphans.

Parity mode (no model load - counts only):

  python scripts/verify_migration.py --parity

Compares per-ctx point counts in Qdrant vs pgvector. Exits 1 if any ctx
count does not match.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from qdrant_client import QdrantClient

QDRANT_URL = "http://localhost:6333"
PG_DSN = os.environ.get("AXON_PG_URL", "postgresql://axon:axon@localhost:5432/axon")


# ---------------------------------------------------------------------------
# Pure parity helpers (no I/O - safe to import in tests)
# ---------------------------------------------------------------------------


def counts_match(qdrant_count: int, pg_count: int) -> bool:
    """Exact row/point-count parity for one ctx."""
    return qdrant_count == pg_count


def parity_summary(per_ctx: dict[str, tuple[int, int]]) -> tuple[bool, str]:
    """Summarize per-ctx (qdrant_count, pg_count) parity. Returns (all_ok, text)."""
    lines: list[str] = []
    all_ok = True
    for ctx, (qn, pn) in sorted(per_ctx.items()):
        ok = counts_match(qn, pn)
        all_ok = all_ok and ok
        lines.append(f"  {ctx}: qdrant={qn} pgvector={pn} -> {'OK' if ok else 'FAIL'}")
    verdict = "PASS" if all_ok else "FAIL"
    return all_ok, f"parity [{verdict}]\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Live backend helpers
# ---------------------------------------------------------------------------


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


def _qdrant_count(client: QdrantClient, ctx: str) -> int:
    """Return the total point count for a Qdrant collection."""
    try:
        info = client.get_collection(ctx)
        return info.points_count or 0
    except Exception:
        return 0


def _pg_count(dsn: str, ctx: str) -> int:
    """Return row count from pgvector embeddings table for a given ctx."""
    import asyncio

    import asyncpg

    async def _fetch() -> int:
        conn = await asyncpg.connect(dsn)
        try:
            row = await conn.fetchrow(
                "SELECT count(*) FROM embeddings WHERE ctx = $1", ctx
            )
            return int(row[0]) if row else 0
        finally:
            await conn.close()

    return asyncio.run(_fetch())


def _known_qdrant_ctxs(client: QdrantClient) -> list[str]:
    """List all collections present in Qdrant."""
    return [c.name for c in client.get_collections().collections]


def main(ctx: str, parity: bool = False) -> None:
    if parity:
        client = QdrantClient(QDRANT_URL)
        ctxs = _known_qdrant_ctxs(client)
        if not ctxs:
            print("No Qdrant collections found.")
            raise SystemExit(1)
        per_ctx: dict[str, tuple[int, int]] = {}
        for c in ctxs:
            qn = _qdrant_count(client, c)
            pn = _pg_count(PG_DSN, c)
            per_ctx[c] = (qn, pn)
        all_ok, text = parity_summary(per_ctx)
        print(text)
        if not all_ok:
            raise SystemExit(1)
        return

    # Default: single-backend Qdrant orphan check
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
    parser = argparse.ArgumentParser(
        description="Scroll a collection and flag orphan file_paths; or check parity between Qdrant and pgvector"
    )
    parser.add_argument("--ctx", default="personal", help="collection name to verify")
    parser.add_argument(
        "--parity",
        action="store_true",
        help="compare point counts across Qdrant and pgvector for all ctxs",
    )
    args = parser.parse_args()
    main(args.ctx, parity=args.parity)
