"""Root-level test configuration.

Test isolation guarantee: AXON_ENGINE is redirected to a per-test
temporary directory so module-level singletons like the TraceStore in
axon/mcp/server.py and axon/hooks/git_event.py never write into the
developer's real ~/.axon data root during tests.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

# Every AXON-owned relational/vector table, truncated between tests so the
# shared Postgres container gives each test a clean slate (the isolation that
# the retired per-test SQLite files used to provide — dec-121 Phase 3).
_AXON_TABLES = (
    "nodes", "edges", "decisions", "adr", "sessions", "session_memory",
    "session_note", "code_change", "file_index", "symbol_deps",
    "failure_record", "outcome_record", "embeddings",
)


@pytest.fixture(scope="session")
def _shared_pg():
    """One Postgres container for the whole test session (or None if unavailable).

    Returns the dsn; tests are isolated by the per-test TRUNCATE in
    _isolate_axon_engine. Degrades to None (no isolation override) when
    testcontainers/docker is absent, so non-Postgres environments still run.
    """
    try:
        from testcontainers.postgres import PostgresContainer
    except Exception:
        yield None
        return
    try:
        with PostgresContainer(
            "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
        ) as pg:
            yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
    except Exception:
        yield None


async def _truncate_all(dsn: str) -> None:
    import asyncpg

    con = await asyncpg.connect(dsn)
    try:
        for table in _AXON_TABLES:
            try:
                await con.execute(f"TRUNCATE {table} CASCADE")
            except asyncpg.UndefinedTableError:
                pass  # created lazily on first use
    finally:
        await con.close()


@pytest.fixture(autouse=True)
def _isolate_axon_engine(
    _shared_pg, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point AXON_ENGINE at a fresh tmp dir for each test.

    The module-level _TRACE_STORE singletons were captured at import time,
    so we also redirect their on-disk path via monkeypatch when the
    relevant modules are already loaded. New trace records appended after
    redirection land under tmp_path; reads of pre-existing records (none
    in a fresh test) are not a concern.
    """
    engine_dir = tmp_path_factory.mktemp("axon_engine")
    monkeypatch.setenv("AXON_ENGINE", str(engine_dir))

    # dec-121 Phase 3: AXON is Postgres-only. Point every test at an isolated
    # shared container and wipe AXON tables before each test for per-test
    # isolation. Tests that need their own container override AXON_PG_URL.
    if _shared_pg is not None:
        monkeypatch.setenv("AXON_PG_URL", _shared_pg)
        asyncio.run(_truncate_all(_shared_pg))

    # Best-effort redirect of the two module-level TraceStore singletons.
    # Import lazily so this conftest doesn't force a load when a test only
    # needs unrelated subsystems.
    import sys

    if "axon.mcp.server" in sys.modules:
        from axon.observability.trace_store import TraceStore as _TS

        srv = sys.modules["axon.mcp.server"]
        if hasattr(srv, "_TRACE_STORE"):
            from types import SimpleNamespace

            monkeypatch.setattr(
                srv, "_TRACE_STORE", _TS(runtime=SimpleNamespace(data_root=engine_dir / "data"))
            )

    if "axon.hooks.git_event" in sys.modules:
        from types import SimpleNamespace

        from axon.observability.trace_store import TraceStore as _TS

        hooks = sys.modules["axon.hooks.git_event"]
        if hasattr(hooks, "_TRACE_STORE"):
            monkeypatch.setattr(
                hooks, "_TRACE_STORE", _TS(runtime=SimpleNamespace(data_root=engine_dir / "data"))
            )

    return engine_dir
