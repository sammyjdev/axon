# Task 0 Digest: File Open Audit Hook Trap

## 1. tests/conftest.py (WHOLE FILE)

```python
"""Root-level test configuration.

Test isolation guarantee: AXON_ENGINE is redirected to a per-test
temporary directory so module-level singletons like the TraceStore in
axon/mcp/server.py and axon/hooks/git_event.py never write into the
developer's real ~/.axon data root during tests.
"""

from __future__ import annotations

import asyncio
import os
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
def _the_suite_never_writes_to_the_operators_files():
    """Fail the run if any test created or grew a file the operator owns.

    Teardown-scoped on purpose: the point is to catch a write nobody predicted, so
    there is nothing to assert until every test has run. It names the offending
    paths rather than the test, because the write usually comes from a module-level
    singleton bound at import, not from the test that happened to trigger it.
    """
    before = _live_file_fingerprint()
    yield
    after = _live_file_fingerprint()
    touched = sorted(
        path for path, size in after.items() if before.get(path) != size
    )
    if touched:
        listed = "\n  ".join(touched[:10])
        more = f"\n  ... and {len(touched) - 10} more" if len(touched) > 10 else ""
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
```

## 2. tests/mcp/test_axon_tools.py - store fixture and test_axon_handoff_includes_context

### store fixture (lines 14-28)

```python
@pytest.fixture
async def store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[SessionStore, None]:
    s = SessionStore(db_path=tmp_path / "axon.db")
    await s.init()
    monkeypatch.setattr(server, "_get_session_store", lambda: s)
    # `axon_handoff` writes its brief to whatever `discover_vault()` resolves, and
    # that is the operator's real AXON_VAULT unless a test says otherwise. Isolating
    # the store alone left every run of this file writing a junk brief into
    # ~/vault/knowledge/handoffs/ - 120 of the 121 files there were test output,
    # committed and pushed hourly. Same fixture shape as test_handoff_persists.py.
    monkeypatch.setattr(server, "discover_vault", lambda **_: tmp_path / "vault")
    yield s
    await s.close()
```

### test_axon_handoff_includes_context (lines 66-70)

```python
async def test_axon_handoff_includes_context(store: SessionStore) -> None:
    await server.axon_capture(summary="a decision", repo="axon")
    brief = await server.axon_handoff(to_agent="codex", repo="axon")
    assert "handoff -> codex" in brief
    assert "a decision" in brief
```

## 3. tests/mcp/test_handoff_persists.py - vault fixture (lines 26-29)

```python
@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(server, "discover_vault", lambda **_: tmp_path)
    return tmp_path
```

## 4. src/axon/observability/trace_store.py - append method (lines 110-113)

```python
def append(self, record: TraceRecord) -> None:
    self._file.parent.mkdir(parents=True, exist_ok=True)
    with self._file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record.model_dump(), sort_keys=True) + "\n")
```

Note: `self._file` is a Path object (line 104). The open call uses `Path.open("a", ...)` which is the pathlib method.

## 5. @traced_tool decorator - part that uses TraceStore

File: `src/axon/observability/traced_tool.py` (lines 197-284)

The decorator does NOT directly call `TraceStore.append`. Instead, it calls `recorder.append_stage(...)` (lines 224, 232, 237, 257, 270), which internally calls `self._store.append(record)` from TraceRecorder (line 71 of trace_store.py):

```python
def traced_tool(
    *,
    risk: RiskClass,
    name: str | None = None,
    store: TraceStore | None = None,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    def decorator(
        fn: Callable[..., Awaitable[Any]],
    ) -> Callable[..., Awaitable[Any]]:
        tool_name = name or fn.__name__
        sig = inspect.signature(fn)

        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            trace_store = _resolve_store(store)
            trace_id = uuid.uuid4().hex
            arg_payload, raw_ctx = _process_arguments(sig, args, kwargs)
            ctx_str = _coerce_ctx(raw_ctx, tool_name=tool_name)

            recorder = trace_store.recorder(
                trace_id=trace_id,
                caller=f"mcp.{tool_name}",
                ctx=ctx_str,
            )

            invoke_payload: TracePayload = {"risk": risk}
            invoke_payload.update(arg_payload)
            recorder.append_stage("invoke", payload=invoke_payload)

            policy_decision = None
            if risk != "read":
                policy = _resolve_policy()
                policy_decision = policy.decide_tool_action(
                    risk=risk, ctx=ctx_str
                )
                recorder.append_policy_decision(policy_decision)
                if not policy_decision.allowed:
                    from axon.policy.core import PolicyDenied

                    exc = PolicyDenied(policy_decision)
                    recorder.append_stage(
                        "error",
                        payload={
                            "ok": False,
                            "latency_ms": 0,
                            "error_type": "PolicyDenied",
                            "error_msg": str(exc)[:200],
                            "reason_code": policy_decision.reason_code.value,
                        },
                    )
                    raise exc

            token = _CURRENT_RECORDER.set(recorder)
            start = time.perf_counter()
            try:
                result = await fn(*args, **kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                latency_ms = int((time.perf_counter() - start) * 1000)
                recorder.append_stage(
                    "error",
                    payload={
                        "ok": False,
                        "latency_ms": latency_ms,
                        "error_type": type(exc).__name__,
                        "error_msg": str(exc)[:200],
                    },
                )
                await _record_tool_failure(tool_name=tool_name, ctx=ctx_str, exc=exc)
                raise
            else:
                latency_ms = int((time.perf_counter() - start) * 1000)
                recorder.append_stage(
                    "output",
                    payload={
                        "ok": True,
                        "latency_ms": latency_ms,
                        "output_tokens": _estimate_tokens(result),
                    },
                )
                return result
            finally:
                _CURRENT_RECORDER.reset(token)

        return wrapper

    return decorator
```

Where TraceRecorder.append_stage calls `self._store.append(record)` at line 71 of trace_store.py.

## 6. Root conftest.py

File exists at repo root. Contents (lines 1-6):

```python
import sys
from pathlib import Path

# Adiciona src/ ao path para que pytest encontre o pacote axon sem instalação
sys.path.insert(0, str(Path(__file__).parent / "src"))
```

## 7. [tool.pytest.ini_options] from pyproject.toml (lines 130-167)

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
testpaths = ["tests"]
pythonpath = ["."]
```

**Pytest configuration: NO xdist, NO addopts.** Child pytest subprocesses must be invoked with standard pytest command; no special flags are pre-configured at the ini level.

## 8. Python Audit Hook Probe Results

Command run:
```python
python3 -c "
import sys, os, tempfile
from pathlib import Path
seen=[]
sys.addaudithook(lambda e,a: seen.append((e,a)) if e=='open' else None)
d=tempfile.mkdtemp()
open(os.path.join(d,'a'),'w').close()
os.close(os.open(os.path.join(d,'b'), os.O_WRONLY|os.O_CREAT))
Path(os.path.join(d,'c')).open('a').close()
Path(os.path.join(d,'e')).write_text('x')
open(os.path.join(d,'a')).close()
for e,a in seen: print(a)
"
```

Output (verbatim):
```
('/var/folders/9v/0ch5sktx4h5cvkx8t5km7_kc0000gn/T/c_8rokka', None, 16780034)
('/var/folders/9v/0ch5sktx4h5cvkx8t5km7_kc0000gn/T/tmpjqb3zqlw/a', 'w', 16778753)
('/var/folders/9v/0ch5sktx4h5cvkx8t5km7_kc0000gn/T/tmpjqb3zqlw/b', None, 16777729)
('/var/folders/9v/0ch5sktx4h5cvkx8t5km7_kc0000gn/T/tmpjqb3zqlw/c', 'a', 16777737)
('/var/folders/9v/0ch5sktx4h5cvkx8t5km7_kc0000gn/T/tmpjqb3zqlw/e', 'w', 16778753)
('/var/folders/9v/0ch5sktx4h5cvkx8t5km7_kc0000gn/T/tmpjqb3zqlw/a', 'r', 16777216)
```

### Tuple Structure Analysis

All six audit events use the tuple format: `(path, mode_or_flags, int_value)`

| Call | API | Tuple | Arg 1 (path) | Arg 2 (write intent) | Arg 2 Type | Arg 3 |
|------|-----|-------|---|---|---|---|
| 1 | `tempfile.mkdtemp()` | `(path, None, 16780034)` | path (str) | None | NoneType | int |
| 2 | `open(path, 'w')` | `(path, 'w', 16778753)` | path (str) | `'w'` | str mode | int |
| 3 | `os.open(path, os.O_WRONLY\|os.O_CREAT)` | `(path, None, 16777729)` | path (str) | None | NoneType | int |
| 4 | `Path.open('a')` | `(path, 'a', 16777737)` | path (str) | `'a'` | str mode | int |
| 5 | `Path.write_text('x')` | `(path, 'w', 16778753)` | path (str) | `'w'` | str mode | int |
| 6 | `open(path)` (read) | `(path, 'r', 16777216)` | path (str) | `'r'` | str mode | int |

### Write Intent Identification

**One line summary:** Built-in `open()` and `Path.open()` and `Path.write_text()` raise audit events with str mode in positional arg[2]; `os.open()` raises with int flags in arg[2] (or None for tempfile/os operations); the str mode value itself ('w', 'a', 'r', etc.) signals write intent.

- **Built-in open()**: tuple arg[1] = str mode (e.g., 'w', 'r', 'a')
- **pathlib.Path.open()**: tuple arg[1] = str mode (e.g., 'w', 'a', 'r')
- **pathlib.Path.write_text()**: tuple arg[1] = str mode ('w')
- **os.open()**: tuple arg[2] = int flags (e.g., os.O_WRONLY|os.O_CREAT)
- **Other os/tempfile ops**: tuple arg[1] = None

Write intent is in **arg[1]** for `open()`/`Path.open()`/`Path.write_text()` (type: str mode), and in **arg[2]** for `os.open()` (type: int flags). Trap: confusing the positional indices between the two raises makes all reads look like writes.
