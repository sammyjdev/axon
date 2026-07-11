"""Index composition drift check for ``pb doctor``.

Warn thresholds are anchored to the measured 2026-07-03 inversion where the
active index was ~97% dev and only ~1.8% vault content.
"""

from __future__ import annotations

import asyncio
import re

import asyncpg

from axon.config.runtime import load_runtime_config
from axon.doctor import CheckResult, CheckStatus

_TABLE_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
# Same short probe budget the MCP health check uses (_PROBE_TIMEOUT): a
# firewalled/hung Postgres must not stall pb doctor (asyncpg default is 60s).
_CONNECT_TIMEOUT_S = 2.0
_VAULT_SHARE_WARN = 0.02
_CAREER_WARN_COUNT = 0
_PLAN_ARTIFACTS_WARN = 1000


async def _fetch_index_composition(*, pg_url: str, table: str) -> dict[str, int]:
    if not _TABLE_RE.fullmatch(table):
        raise ValueError(f"invalid table name {table!r}")
    con = await asyncio.wait_for(asyncpg.connect(pg_url), timeout=_CONNECT_TIMEOUT_S)
    try:
        row = await con.fetchrow(
            f"""
            SELECT
                COUNT(*)::bigint AS total_chunks,
                COUNT(*) FILTER (WHERE file_path LIKE '%/vault/%')::bigint AS vault_chunks,
                COUNT(*) FILTER (WHERE ctx = 'career')::bigint AS career_chunks,
                COUNT(*) FILTER (WHERE file_path LIKE '%/plans/%')::bigint AS plan_artifacts
            FROM {table}
            """  # noqa: S608
        )
    finally:
        await con.close()
    return {
        "total_chunks": int(row["total_chunks"]),
        "vault_chunks": int(row["vault_chunks"]),
        "career_chunks": int(row["career_chunks"]),
        "plan_artifacts": int(row["plan_artifacts"]),
    }


def check_index_composition(*, pg_url: str | None = None, table: str = "embeddings") -> CheckResult:
    resolved_pg_url = pg_url or load_runtime_config().pg_url
    try:
        stats = asyncio.run(_fetch_index_composition(pg_url=resolved_pg_url, table=table))
    except (TimeoutError, OSError, ValueError, asyncpg.PostgresError):
        return CheckResult(
            name="index.composition",
            status=CheckStatus.WARN,
            detail="skipped: db unreachable",
        )

    total = stats["total_chunks"]
    vault = stats["vault_chunks"]
    career = stats["career_chunks"]
    plans = stats["plan_artifacts"]
    vault_share = (vault / total) if total else 0.0
    detail = f"total={total} vault_share={vault_share * 100:.1f}% career={career} plans={plans}"
    if (
        vault_share < _VAULT_SHARE_WARN
        or career == _CAREER_WARN_COUNT
        or plans > _PLAN_ARTIFACTS_WARN
    ):
        return CheckResult(
            name="index.composition",
            status=CheckStatus.WARN,
            detail=detail,
            suggestion=(
                "Check index routing and corpus mix - vault share is low, career is empty, "
                "or plan artifacts are dominating."
            ),
        )
    return CheckResult(
        name="index.composition",
        status=CheckStatus.OK,
        detail=detail,
    )
