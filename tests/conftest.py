"""Root-level test configuration.

Test isolation guarantee: AXON_ENGINE is redirected to a per-test
temporary directory so module-level singletons like the TraceStore in
axon/mcp/server.py and axon/hooks/git_event.py never write into the
developer's real ~/.axon data root during tests.
"""

from __future__ import annotations

import asyncio
import os
import sys
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


#: Deliberately unroutable: port 1 on loopback, and a database name that says
#: why if it ever surfaces in an error. Used when no test container is
#: available, so a missing docker degrades to a clear connection failure rather
#: than silently borrowing the operator's database.
_UNREACHABLE_PG_URL = "postgresql://axon:axon@127.0.0.1:1/axon_tests_have_no_database"

#: The operator's real DSN as it looked before any fixture touched it. Kept so
#: a test can assert the suite never points back at it.
_OPERATOR_PG_URL = os.environ.get("AXON_PG_URL")

#: The operator's real vault and engine root, captured at import for the same
#: reason. The DSN guard below was written after the suite was caught writing to
#: the live database; it covers only AXON_PG_URL, so the suite went on writing
#: FILES into the operator's environment unnoticed - 120 of the 121 handoff briefs
#: in ~/vault/knowledge/handoffs/ were test output, committed and pushed hourly.
_OPERATOR_VAULT = os.environ.get("AXON_VAULT")
_OPERATOR_ENGINE = os.environ.get("AXON_ENGINE")

#: Paths THIS pytest process opened for writing, recorded by the audit hook
#: below so teardown can tell a suite write from an external writer's.
_suite_write_opens: set[str] = set()

#: Open flags that indicate write intent; OR-ed mask, tested with `&`.
_WRITE_OPEN_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
_WRITE_MODE_CHARS = frozenset("wax+")


def _record_write_opens(event: str, args: tuple[object, ...]) -> None:
    # CPython's "open" audit event args are (path, mode_or_None, flags_int):
    # mode is None for os.open (a flags-only API) and a str like "w" for
    # builtins.open / Path.open. args[2] is always real open flags, never a
    # permission mode such as 0o666, whose O_RDWR bit would mark every read
    # as a write. Other raisers of this event exist, so stay defensive.
    try:
        if event != "open" or not args:
            return
        mode = args[1] if len(args) > 1 else None
        flags = args[2] if len(args) > 2 else None
        by_flags = isinstance(flags, int) and bool(flags & _WRITE_OPEN_FLAGS)
        by_mode = isinstance(mode, str) and bool(_WRITE_MODE_CHARS.intersection(mode))
        if by_flags or by_mode:
            _suite_write_opens.add(os.path.abspath(os.fsdecode(args[0])))
    except Exception:  # noqa: S110 - an audit hook must never break the audited call
        pass


def _live_file_fingerprint() -> dict[str, int]:
    """(path -> size) for the operator's own files a test must never touch.

    Size, not just presence: `data/compression/stats.jsonl` is appended to rather
    than created, so a path-set comparison would miss it (issue #203).
    """
    roots: list[Path] = []
    if _OPERATOR_VAULT:
        roots.append(Path(_OPERATOR_VAULT).expanduser() / "knowledge" / "handoffs")
    if _OPERATOR_ENGINE:
        data = Path(_OPERATOR_ENGINE).expanduser() / "data"
        roots.extend([data / "compression", data / "recall", data / "trace"])

    fingerprint: dict[str, int] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            try:
                if path.is_file():
                    fingerprint[str(path)] = path.stat().st_size
            except OSError:
                continue
    return fingerprint


@pytest.fixture(scope="session", autouse=True)
def _the_suite_never_writes_to_the_operators_files(
    request: pytest.FixtureRequest,
):
    """Fail the run if any test created or grew a file the operator owns.

    Teardown-scoped on purpose: the point is to catch a write nobody predicted, so
    there is nothing to assert until every test has run. It names the offending
    paths rather than the test, because the write usually comes from a module-level
    singleton bound at import, not from the test that happened to trigger it.

    Only writes made by THIS process fail the run (attributed via an audit
    hook); a concurrent external writer - the operator's MCP server or git
    hooks - is reported on stderr without failing, so an agent loop running
    alongside the suite cannot turn a green run red.
    """
    sys.addaudithook(_record_write_opens)
    before = _live_file_fingerprint()
    yield
    after = _live_file_fingerprint()
    touched = sorted(
        path for path, size in after.items() if before.get(path) != size
    )
    ours = [p for p in touched if os.path.abspath(p) in _suite_write_opens]
    external = [p for p in touched if os.path.abspath(p) not in _suite_write_opens]
    if external:
        listed = "\n  ".join(external[:10])
        more = f"\n  ... and {len(external) - 10} more" if len(external) > 10 else ""
        note = (
            "note: these operator files were changed by an external writer, "
            f"not by the suite:\n  {listed}{more}\n"
        )
        # Session-fixture teardown runs under pytest's global capture, which
        # discards stderr on a green run; suspend it so the note reaches the
        # real stderr (no-op under -s, where nothing is captured).
        capman = request.config.pluginmanager.get_plugin("capturemanager")
        if capman is not None:
            capman.suspend_global_capture()
        try:
            print(note, file=sys.stderr, end="")
        finally:
            if capman is not None:
                capman.resume_global_capture()
    if ours:
        listed = "\n  ".join(ours[:10])
        more = f"\n  ... and {len(ours) - 10} more" if len(ours) > 10 else ""
        raise AssertionError(
            "the suite wrote into the operator's own files - isolate the fixture "
            f"that resolves these paths:\n  {listed}{more}"
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
def _isolate_global_git_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's global/system git config out of temp repos (#115).

    Tests that `git init` a temp directory sit outside this repo's local
    `commit.gpgsign=false` override and inherit the global one. With signing on
    and the key unavailable, every `git commit` in the suite fails. CI never saw
    it because CI has no signing config at all.
    """
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)


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
    # The test suite must never load a real cross-encoder; tests wanting
    # rerank set it to "1" in their body.
    monkeypatch.setenv("AXON_RERANK", "0")

    # dec-121 Phase 3: AXON is Postgres-only. Point every test at an isolated
    # shared container and wipe AXON tables before each test for per-test
    # isolation. Tests that need their own container override AXON_PG_URL.
    if _shared_pg is not None:
        monkeypatch.setenv("AXON_PG_URL", _shared_pg)
        asyncio.run(_truncate_all(_shared_pg))
    else:
        # No container: without this the operator's own AXON_PG_URL survives and
        # the suite writes into the live decision store. It did - a 2026-08-29
        # audit found rows keyed `myrepo`, `other`, `edgesrepo`, `linkrepo` and
        # `openrouter_deepseek_deepseek-v4-flash-r2` in the real database, all
        # fixture names. Point at a DSN that cannot resolve instead: tests that
        # need Postgres fail loudly, tests that do not keep running, which is
        # what the "non-Postgres environments still run" note above intends.
        monkeypatch.setenv("AXON_PG_URL", _UNREACHABLE_PG_URL)

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

        # Same import-time binding, same fix: _COMPRESSION_TELEMETRY resolved
        # data_root when the module loaded, so the per-test AXON_ENGINE below
        # never reached it and every run appended to the operator's own
        # data/compression/stats.jsonl - the records `axon doctor` and
        # `axon gain` read, and the ones issue #168 is investigating.
        if hasattr(srv, "_COMPRESSION_TELEMETRY"):
            from types import SimpleNamespace

            from axon.observability.compression_telemetry import (
                CompressionTelemetryStore as _CTS,
            )

            monkeypatch.setattr(
                srv,
                "_COMPRESSION_TELEMETRY",
                _CTS(SimpleNamespace(data_root=engine_dir / "data")),
            )

    # `axon.cli.pb` binds its own `_RUNTIME` at import (pb.py:50) and builds a
    # TraceStore from it per search, so CLI tests appended to the operator's real
    # data/trace/records.jsonl. Third module with this shape, and the session
    # guard above is what turned it from invisible into a failing run.
    #
    # Replace the whole object, never mutate it: RuntimeConfig is a frozen
    # dataclass whose `data_root` is a property derived from `engine_root`, so
    # setattr on the shared instance raises and takes every test with it.
    if "axon.cli.pb" in sys.modules:
        import dataclasses

        pb = sys.modules["axon.cli.pb"]
        if hasattr(pb, "_RUNTIME"):
            monkeypatch.setattr(
                pb, "_RUNTIME", dataclasses.replace(pb._RUNTIME, engine_root=engine_dir)
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
