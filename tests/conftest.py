"""Root-level test configuration.

Test isolation guarantee: AXON_ENGINE is redirected to a per-test
temporary directory so module-level singletons like the TraceStore in
axon/mcp/server.py and axon/hooks/git_event.py never write into the
developer's real ~/.axon data root during tests.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_axon_engine(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
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

    # Pin the per-concern storage backends to sqlite for tests by default. After
    # the dec-121 step-3 cutover the production defaults are postgres, but tests
    # use isolated per-test SQLite stores (the postgres repositories are covered
    # separately via testcontainers). Tests that specifically exercise postgres
    # selection set or delenv these explicitly and override this default.
    monkeypatch.setenv("AXON_GRAPH_BACKEND", "sqlite")
    monkeypatch.setenv("AXON_DECISIONS_BACKEND", "sqlite")
    monkeypatch.setenv("AXON_FILEINDEX_BACKEND", "sqlite")
    monkeypatch.setenv("AXON_SESSIONS_BACKEND", "sqlite")

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
        from axon.observability.trace_store import TraceStore as _TS
        from types import SimpleNamespace

        hooks = sys.modules["axon.hooks.git_event"]
        if hasattr(hooks, "_TRACE_STORE"):
            monkeypatch.setattr(
                hooks, "_TRACE_STORE", _TS(runtime=SimpleNamespace(data_root=engine_dir / "data"))
            )

    return engine_dir
