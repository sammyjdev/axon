"""The suite must never point at the operator's own database.

`_shared_pg` degrades to None when docker or testcontainers is absent, and the
per-test override was inside `if _shared_pg is not None`. So on a machine with
no container the operator's `AXON_PG_URL` survived into every test, and the
suite wrote to the live decision store.

It did. An audit of the real database on 2026-08-29 found rows keyed `myrepo`,
`other`, `edgesrepo`, `linkrepo`, `resumerepo`, `_common-errors` and
`openrouter_deepseek_deepseek-v4-flash-r2` - every one a fixture name, none a
repository that exists. Raised by the GPT-family cross-review before the audit
confirmed it: `tests/cli/test_rekey_repo_cli.py` runs a destructive migration
against whatever DSN it is handed.
"""

from __future__ import annotations

import os

from tests.conftest import _OPERATOR_PG_URL, _UNREACHABLE_PG_URL


def test_the_active_dsn_is_never_the_operators() -> None:
    active = os.environ.get("AXON_PG_URL")

    if _OPERATOR_PG_URL is None:
        # Nothing configured outside the suite; there is no live store to hit.
        return

    assert active != _OPERATOR_PG_URL, (
        "the suite is pointed at the operator's own database - a destructive "
        "test would run against real decisions"
    )


def test_the_fallback_dsn_cannot_reach_anything() -> None:
    """Port 1 on loopback, so the degraded path fails loudly rather than quietly."""
    assert ":1/" in _UNREACHABLE_PG_URL
    assert "127.0.0.1" in _UNREACHABLE_PG_URL


def test_the_outcome_store_follows_the_active_dsn() -> None:
    """`server._outcome_store` is built from the import-time `_RUNTIME.pg_url`.

    Fourth module with the shape `_TRACE_STORE`, `_COMPRESSION_TELEMETRY` and
    `pb._RUNTIME` already had: the value is resolved when the module loads, so
    the per-test `AXON_PG_URL` override never reaches it. `axon_record_outcome`
    therefore wrote into the operator's live database. It did: on 2026-09-13,
    21 of the 66 rows in the real `outcome_record` were `project='outcome_target'`,
    the tmp_path fixture repo of `tests/mcp/test_repo_resolution.py`.
    """
    from axon.mcp import server

    assert server._get_outcome_store()._dsn == os.environ["AXON_PG_URL"], (
        "the outcome store is bound to a DSN the suite did not choose - "
        "axon_record_outcome writes into whatever database was configured at import"
    )
