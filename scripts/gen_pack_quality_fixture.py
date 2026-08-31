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
import re
import sys
from collections.abc import Iterable, Mapping
from datetime import datetime
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
      AND created_at <= $1
    ORDER BY created_at DESC, id DESC
    LIMIT $2
"""


#: /Users/<name>/ and /home/<name>/ - the only part of an absolute path that
#: carries a local username.
_HOME_PREFIX_RE = re.compile(r"^/(?:Users|home)/[^/]+/")


def _portable_path(path: str) -> str:
    """Strip the machine-specific prefix, keep everything that identifies the file.

    This used to reduce an absolute path to its basename, which kept the username
    out of a committed artifact and was metric-preserving while scoring compared
    basenames. Under path-suffix scoring it is not: `pyproject.toml` on its own
    matches every indexed repo. Cutting only the home prefix satisfies both.
    """
    if not path.startswith("/"):
        return path
    stripped = _HOME_PREFIX_RE.sub("", path, count=1)
    return stripped if stripped != path else path.lstrip("/")


#: Paths the embedder pipeline never indexes, so no retrieval can reach them.
#: `.specs/` is gitignored; `docs/superpowers/plans/` is in
#: _EXCLUDED_PATH_PATTERNS because indexing eval artifacts leaks answers into
#: the measurement instrument. Note `docs/superpowers/specs/` IS indexed.
_UNINDEXABLE_PREFIXES: tuple[str, ...] = (
    ".specs/",
    "docs/superpowers/plans/",
)


def is_unindexable(path: str) -> bool:
    """Whether the pipeline excludes this path from the index by policy."""
    normalized = path.lstrip("/")
    return normalized.startswith(_UNINDEXABLE_PREFIXES)


def build_cases(rows: Iterable[Mapping[str, Any]]) -> list[dict]:
    """Map decision rows to fixture cases, dropping what cannot be scored.

    A case whose expected files are all unindexable measures the indexing
    policy, not the retrieval, so it is not a case. In a mixed case only the
    unreachable file is pruned - the decision still scores on the rest.
    """
    cases: list[dict] = []
    for row in rows:
        paths = [_portable_path(f) for f in json.loads(row["files"])]
        reachable = [p for p in paths if not is_unindexable(p)]
        if not reachable:
            continue
        cases.append(
            {
                "query": row["summary"],
                "expected_files": reachable,
                "decision_id": row["id"],
            }
        )
    return cases


def write_cases(cases: list[dict], out: Path, *, provenance: dict | None = None) -> None:
    """Write the fixture, with the parameters that produced it.

    Provenance travels with the corpus so a regeneration is checkable: same
    cutoff and limit against the same store must yield the same cases. Without
    it, `ORDER BY created_at DESC LIMIT n` silently returns a different corpus
    each time a decision lands, and two runs weeks apart are not comparable.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"provenance": provenance or {}, "cases": cases}
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _unqualified_cases(cases: list[dict]) -> list[str]:
    """Cases with an expected file carrying no directory at all.

    Scoring matches on path suffix, so `src/axon/store/__init__.py` is exact.
    A bare `pyproject.toml` is not: it matches that file in every indexed repo.
    These come from decisions that recorded the filename without a directory -
    the ambiguity is in the source data and cannot be repaired here, only
    reported.
    """
    return [
        case["decision_id"]
        for case in cases
        if any("/" not in path for path in case["expected_files"])
    ]


async def main() -> None:
    import asyncpg

    parser = argparse.ArgumentParser(description="Build the pack-quality fixture.")
    parser.add_argument("--limit", type=int, default=120)
    parser.add_argument(
        "--cutoff",
        required=True,
        help=(
            "ISO timestamp; only decisions created at or before it enter the corpus. "
            "Required on purpose: an unbounded corpus is not a ruler."
        ),
    )
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument(
        "--dsn", default=os.environ.get("AXON_PG_URL", "postgresql://axon:axon@localhost:5434/axon")
    )
    args = parser.parse_args()

    cutoff = datetime.fromisoformat(args.cutoff.replace("Z", "+00:00"))
    con = await asyncpg.connect(args.dsn)
    try:
        rows = await con.fetch(QUERY, cutoff, args.limit)
    finally:
        await con.close()

    cases = build_cases(rows)
    write_cases(
        cases,
        args.out,
        provenance={
            "cutoff": args.cutoff,
            "limit": args.limit,
            "repo": "axon",
            "matching": "path-suffix on repo-relative expected_files",
        },
    )
    print(f"wrote {len(cases)} cases to {args.out} (cutoff {args.cutoff})")

    unqualified = _unqualified_cases(cases)
    if unqualified:
        print(
            f"note: {len(unqualified)} of {len(cases)} case(s) expect a bare filename "
            f"with no directory; those still match across repos: {sorted(unqualified)[:6]}"
        )


if __name__ == "__main__":
    asyncio.run(main())
