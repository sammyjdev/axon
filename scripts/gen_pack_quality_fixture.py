"""Generate the pack-quality case fixture ONCE, from the decision store.

The fixture is committed and read by the eval. It is deliberately not queried
live: a ruler that grows new cases every week silently stops being comparable
to last week's run, which is how docs/METRICS.md ended up publishing a p50 over
a filter that selected the events that had worked.

Usage:
    AXON_PG_URL=postgresql://axon:axon@localhost:5434/axon \
        python3 scripts/gen_pack_quality_fixture.py --limit 120
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

OUT = REPO_ROOT / "tests" / "benchmark" / "fixtures" / "pack_quality_golden.json"

QUERY = """
    SELECT id,
           frontmatter->>'summary' AS summary,
           frontmatter->'files'    AS files
    FROM decisions
    WHERE frontmatter->>'repo' = 'axon'
      AND jsonb_array_length(COALESCE(frontmatter->'files', '[]')) BETWEEN 1 AND 6
      AND length(frontmatter->>'summary') > 25
    ORDER BY created_at DESC
    LIMIT $1
"""


def build_cases(rows: Iterable[Mapping[str, Any]]) -> list[dict]:
    """Map decision rows to fixture cases."""
    return [
        {
            "query": row["summary"],
            "expected_files": json.loads(row["files"]),
            "decision_id": row["id"],
        }
        for row in rows
    ]


def write_cases(cases: list[dict], out: Path) -> None:
    """Write the fixture. mkdir -p the parent first, same as today."""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cases, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


async def main() -> None:
    import asyncpg

    parser = argparse.ArgumentParser(description="Build the pack-quality fixture.")
    parser.add_argument("--limit", type=int, default=120)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument(
        "--dsn", default=os.environ.get("AXON_PG_URL", "postgresql://axon:axon@localhost:5434/axon")
    )
    args = parser.parse_args()

    con = await asyncpg.connect(args.dsn)
    try:
        rows = await con.fetch(QUERY, args.limit)
    finally:
        await con.close()

    cases = build_cases(rows)
    write_cases(cases, args.out)
    print(f"wrote {len(cases)} cases to {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
