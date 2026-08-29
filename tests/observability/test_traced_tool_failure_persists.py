"""A tool failure must actually reach the database, not just the code path.

The existing tests for this feature all substitute a sink for
`_resolve_failure_store`, so they prove the call is made and nothing about
whether it lands. It did not: `_record_tool_failure` builds a `FailureStore`
and calls `save_failure` without ever calling `init()`, which is the only
thing that runs the `CREATE TABLE IF NOT EXISTS`. The single call site that
does init is `ExpansionService`, and `grep -rn "ExpansionService(" src/`
returns nothing - it is constructed only in its own tests.

So on every real install the insert hit `UndefinedTableError`, which the
deliberately broad `except Exception: logger.debug(...)` swallowed. The
feature persisted nothing, anywhere, and the suite could not see it.

Found by the GPT-family cross-review, 2026-08-27.
"""

from __future__ import annotations

import asyncpg
import pytest

from axon.config.runtime import load_runtime_config
from axon.observability.traced_tool import _record_tool_failure


async def _drop_table(dsn: str) -> None:
    con = await asyncpg.connect(dsn)
    try:
        await con.execute("DROP TABLE IF EXISTS failure_record")
    finally:
        await con.close()


async def _count_rows(dsn: str, operation: str) -> int:
    con = await asyncpg.connect(dsn)
    try:
        return await con.fetchval(
            "SELECT count(*) FROM failure_record WHERE operation = $1", operation
        )
    finally:
        await con.close()


async def test_a_tool_failure_lands_in_the_table_from_a_cold_schema() -> None:
    """Cold schema on purpose: the table's absence is the whole defect."""
    dsn = getattr(load_runtime_config(), "pg_url", None)
    if not dsn:
        pytest.skip("no pg_url configured")

    await _drop_table(dsn)

    await _record_tool_failure(
        tool_name="test_cold_schema_tool",
        ctx="personal",
        exc=RuntimeError("boom"),
    )

    assert await _count_rows(dsn, "test_cold_schema_tool") == 1, (
        "the failure was swallowed - the table is created by FailureStore.init(), "
        "which nothing on this path calls"
    )
