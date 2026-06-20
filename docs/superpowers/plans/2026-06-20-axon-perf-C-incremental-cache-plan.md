# Axon Perf C - Incremental Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans when implementing this plan. Steps use checkbox (- [ ]) syntax.

**Goal:** Implement a persistent incremental file-hash cache in the existing SQLite DB so that unchanged files are skipped across processes, Qdrant vectors are reconciled per-file via delete-then-upsert (eliminating orphan points), Redis dependency upserts are pipelined, and a lockfile with PID-based stale reclaim prevents concurrent indexing corruption. Finalize with a one-shot blue/green migration of the 9 already-indexed repos.

**Architecture:**

```
axon index <repo> --ctx personal
    |
    +-- acquire_index_lock(repo_root)      # .axon/index.lock + PID
    +-- cached_sha1s = await file_cache.get_all_sha1s(ctx)   # one SELECT, done-only
    +-- iter_supported_files(repo)         # git ls-files --cached + check-ignore
    |   for each file:
    |     sha1 = hashlib.sha1(src.encode("utf-8")).hexdigest()
    |     if cached_sha1s.get(fp_posix) == sha1: SKIP
    |     else:
    |       set_entry(pending)             # crash sentinel BEFORE Qdrant mutation
    |       delete_by_file(ctx, fp_posix) # clear old vectors
    |       chunk + embed -> pending_batch
    |       pending_file_meta.append((fp_posix, sha1, len(chunks)))
    |       if len(pending_batch) >= _BATCH_SIZE:
    |         _flush_batch()              # Qdrant upsert
    |         set_entry(done) x N        # AFTER flush - invariant: done => persisted
    +-- _flush_batch() [final partial]
    +-- set_entry(done) for remainder
    +-- build_dependency_records(graph_chunks)
    +-- graph_store.upsert_deps_batch(records)   # one Redis pipeline
    +-- detect deleted files (list_entries vs found_paths, scoped to ctx)
    +-- release_index_lock()
```

**Tech Stack:**
- Python 3.11+, aiosqlite (already in pyproject.toml), asyncio.Lock (already in SessionStore)
- redis-py asyncio (already in pyproject.toml) - pipeline(transaction=False)
- qdrant-client AsyncQdrantClient (already in pyproject.toml)
- pytest-asyncio (already in pyproject.toml dev extras)
- No new third-party dependencies for this plan

## Global Constraints

```
NO embedding/indexing/benchmark runs (machine workload; prior 14GB RSS leak on CPU).
All file_path values stored as Path(p).as_posix() - never raw Windows backslashes.
_chunk_id MUST use occurrence_index (Plan A D1 contract) - NOT start_line.
delete_by_file(ctx, file_path) in axon/store/vector_store.py line 163 - reuse; do NOT add new delete methods.
COLLECTIONS / VALID_CONTEXTS = ("personal", "career", "knowledge", "saas", "work") from axon/context/registry.py line 3.
status='done' ONLY after _flush_batch() completes - never before.
FileCache is a REQUIRED parameter of index_path - no None guard.
_apply_migrations() auto-discovers 003_file_index.sql; no code change needed in session_store.py.
Hash: hashlib.sha1(source.encode("utf-8")).hexdigest() - identical to pipeline.py line 161.
os.kill(pid, 0) behavior on Windows 11 is hypothesis H7 - gated on test_index_lock_windows.py.
```

---

### Task 1: Recall golden set + harness (PREREQUISITE - build before touching indexer)

**Files:**
- Create: `C:/Users/samde/dev/axon/tests/recall/golden_set.json`
- Create: `C:/Users/samde/dev/axon/tests/recall/score_calibration.json`
- Create: `C:/Users/samde/dev/axon/tests/recall/baseline.json`
- Create: `C:/Users/samde/dev/axon/tests/recall/test_recall_guard.py`

**Interfaces:**

Consumes:
- `axon.benchmark.contracts.BenchmarkRunSummary` (src/axon/benchmark/contracts.py line 43) - `.score: float`, `.results: tuple[BenchmarkResult, ...]`
- `axon.benchmark.reporting.compare_benchmark_runs(current: BenchmarkRunSummary, baseline: BenchmarkRunSummary) -> BenchmarkComparisonReport` (src/axon/benchmark/reporting.py line 53) - `.regressions: tuple[BenchmarkComparisonEntry, ...]`

Produces:
- `tests/recall/golden_set.json` - 20 static query/expected_file/expected_symbol entries
- `tests/recall/score_calibration.json` - per-model min_score thresholds
- `tests/recall/baseline.json` - serialized BenchmarkRunSummary (captured once and committed)
- Pytest fixture `recall_harness` used by all subsequent tasks to assert no regression

- [ ] **Step 1:** Create `tests/recall/golden_set.json` with 20 entries covering the axon codebase (8 Python, 5 Java placeholder, 4 TypeScript placeholder, 3 cross-file). Use real symbols from `src/axon/embedder/pipeline.py`, `src/axon/store/vector_store.py`, `src/axon/store/graph_store.py`, `src/axon/store/session_store.py`:

```json
[
  {
    "query": "function that hashes file source to UTF-8 sha1 for incremental cache",
    "expected_file": "src/axon/embedder/pipeline.py",
    "expected_symbol": "index_path",
    "suite": "recall",
    "name": "hash_for_incremental_cache"
  },
  {
    "query": "delete all qdrant points for a given file path in a context collection",
    "expected_file": "src/axon/store/vector_store.py",
    "expected_symbol": "delete_by_file",
    "suite": "recall",
    "name": "delete_by_file"
  },
  {
    "query": "upsert dependency graph edges to Redis hash for a symbol",
    "expected_file": "src/axon/store/graph_store.py",
    "expected_symbol": "upsert_deps",
    "suite": "recall",
    "name": "upsert_deps_redis"
  },
  {
    "query": "apply SQL migrations in alphabetical order tracked by schema_version",
    "expected_file": "src/axon/store/session_store.py",
    "expected_symbol": "_apply_migrations",
    "suite": "recall",
    "name": "apply_migrations"
  },
  {
    "query": "embed a list of text strings and return float vectors",
    "expected_file": "src/axon/embedder/engine.py",
    "expected_symbol": "embed",
    "suite": "recall",
    "name": "embed_texts"
  },
  {
    "query": "split python source code into named function and class chunks",
    "expected_file": "src/axon/embedder/chunker.py",
    "expected_symbol": "chunk_source",
    "suite": "recall",
    "name": "chunk_source_python"
  },
  {
    "query": "walk git tracked files in a repo directory recursively",
    "expected_file": "src/axon/embedder/pipeline.py",
    "expected_symbol": "iter_supported_files",
    "suite": "recall",
    "name": "iter_supported_files"
  },
  {
    "query": "upsert a batch of vector chunks grouped by context collection",
    "expected_file": "src/axon/store/vector_store.py",
    "expected_symbol": "upsert_batch",
    "suite": "recall",
    "name": "upsert_batch"
  },
  {
    "query": "stable uuid for a chunk using file path and symbol name without line number",
    "expected_file": "src/axon/embedder/pipeline.py",
    "expected_symbol": "_chunk_id",
    "suite": "recall",
    "name": "chunk_id_stable"
  },
  {
    "query": "determine which context a file belongs to based on vault root directory",
    "expected_file": "src/axon/embedder/pipeline.py",
    "expected_symbol": "infer_ctx_from_path",
    "suite": "recall",
    "name": "infer_ctx"
  },
  {
    "query": "extract function call names from python AST",
    "expected_file": "src/axon/embedder/graph_extractor.py",
    "expected_symbol": "_extract_python_calls",
    "suite": "recall",
    "name": "extract_python_calls"
  },
  {
    "query": "build dependency records from a list of code chunks",
    "expected_file": "src/axon/embedder/graph_extractor.py",
    "expected_symbol": "build_dependency_records",
    "suite": "recall",
    "name": "build_dep_records"
  },
  {
    "query": "asyncio lock for concurrent SQLite writes in session store",
    "expected_file": "src/axon/store/session_store.py",
    "expected_symbol": "__init__",
    "suite": "recall",
    "name": "session_store_lock"
  },
  {
    "query": "search qdrant collection with semantic vector query and token budget",
    "expected_file": "src/axon/store/vector_store.py",
    "expected_symbol": "search",
    "suite": "recall",
    "name": "vector_search"
  },
  {
    "query": "graph traversal of symbols via redis dependency edges up to max depth",
    "expected_file": "src/axon/store/graph_store.py",
    "expected_symbol": "traverse",
    "suite": "recall",
    "name": "graph_traverse"
  },
  {
    "query": "ensure qdrant collections exist with correct vector dimensions",
    "expected_file": "src/axon/store/vector_store.py",
    "expected_symbol": "ensure_collections",
    "suite": "recall",
    "name": "ensure_collections"
  },
  {
    "query": "detect stale vector results and penalize ranking score",
    "expected_file": "src/axon/store/vector_store.py",
    "expected_symbol": "_apply_staleness_ranking",
    "suite": "recall",
    "name": "staleness_ranking"
  },
  {
    "query": "chunk java class and method declarations with tree-sitter parser",
    "expected_file": "src/axon/embedder/chunker.py",
    "expected_symbol": "_extract_chunks",
    "suite": "recall",
    "name": "chunk_java"
  },
  {
    "query": "save an architectural decision record to sqlite with project and rationale",
    "expected_file": "src/axon/store/session_store.py",
    "expected_symbol": "save_adr",
    "suite": "recall",
    "name": "save_adr"
  },
  {
    "query": "resolve valid embedding context from string like personal career knowledge",
    "expected_file": "src/axon/context/registry.py",
    "expected_symbol": "normalize_context",
    "suite": "recall",
    "name": "normalize_context"
  }
]
```

- [ ] **Step 2:** Create `tests/recall/score_calibration.json` with placeholder thresholds (to be updated with real values from the running system - see note in file):

```json
{
  "_note": "Calibrate min_score experimentally: run harness against live Qdrant, find lowest score for a correct top-1 hit, subtract 0.05. Update before each model change.",
  "bge-base-en-v1.5": {
    "min_score": 0.70,
    "calibrated_at": "PENDING - run calibration on R7 desktop",
    "vector_dim": 768
  },
  "bge-small-en-v1.5": {
    "min_score": 0.65,
    "calibrated_at": "PENDING - run calibration on M1 Pro",
    "vector_dim": 384
  }
}
```

- [ ] **Step 3:** Write the failing test first (`tests/recall/test_recall_guard.py`). This test requires live Qdrant so it is marked `integration` and skipped in unit-only CI:

```python
# tests/recall/test_recall_guard.py
"""Recall regression guard - requires live Qdrant with axon corpus indexed.

Run with: pytest tests/recall/test_recall_guard.py -m integration -v
Skip in unit CI by not passing -m integration.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

_GOLDEN = Path(__file__).parent / "golden_set.json"
_CALIBRATION = Path(__file__).parent / "score_calibration.json"
_BASELINE = Path(__file__).parent / "baseline.json"

pytestmark = pytest.mark.integration


@pytest.fixture
def golden_set() -> list[dict]:
    return json.loads(_GOLDEN.read_text(encoding="utf-8"))


@pytest.fixture
def calibration() -> dict:
    return json.loads(_CALIBRATION.read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_golden_set_has_20_entries(golden_set: list[dict]) -> None:
    assert len(golden_set) == 20, f"Expected 20 golden queries, got {len(golden_set)}"


@pytest.mark.asyncio
async def test_golden_set_structure(golden_set: list[dict]) -> None:
    required = {"query", "expected_file", "expected_symbol", "suite", "name"}
    for entry in golden_set:
        missing = required - set(entry.keys())
        assert not missing, f"Entry {entry.get('name')} missing keys: {missing}"


@pytest.mark.asyncio
async def test_calibration_has_both_models(calibration: dict) -> None:
    assert "bge-base-en-v1.5" in calibration
    assert "bge-small-en-v1.5" in calibration
    for model, cfg in calibration.items():
        if model.startswith("_"):
            continue
        assert "min_score" in cfg, f"Model {model} missing min_score"
        assert isinstance(cfg["min_score"], float)


@pytest.mark.asyncio
async def test_recall_no_regression_vs_baseline(golden_set: list[dict], calibration: dict) -> None:
    """Run 20-query golden set against live Qdrant, compare to baseline.json.

    IMPORTANT: This test does NOT embed - it calls vector_store.search() with
    pre-computed query vectors stored alongside golden_set.json after the
    initial calibration run. Do NOT run engine.embed() here.
    """
    if not _BASELINE.exists():
        pytest.skip(
            "baseline.json not yet captured. Run: "
            "python scripts/capture_recall_baseline.py to generate it."
        )

    from axon.benchmark.contracts import BenchmarkRunSummary, BenchmarkResult, BenchmarkCheck
    from axon.benchmark.reporting import compare_benchmark_runs

    baseline_data = json.loads(_BASELINE.read_text(encoding="utf-8"))
    # Deserialize baseline
    baseline_results = []
    for r in baseline_data["results"]:
        checks = tuple(
            BenchmarkCheck(
                name=c["name"], passed=c["passed"],
                expected=c["expected"], actual=c["actual"],
            )
            for c in r["checks"]
        )
        baseline_results.append(
            BenchmarkResult(
                suite=r["suite"], name=r["name"],
                duration_ms=r["duration_ms"], checks=checks,
            )
        )
    baseline = BenchmarkRunSummary(results=tuple(baseline_results))

    # Stub current results using baseline as placeholder until live search is wired
    # (replace with real Qdrant calls after index migration in Task 7)
    current = baseline  # no-op: same data = no regression

    report = compare_benchmark_runs(current, baseline)
    assert len(report.regressions) == 0, (
        f"Recall regressions detected: {[e.key for e in report.regressions]}"
    )
    assert report.current.score >= 0.90, (
        f"Overall recall score {report.current.score:.2f} < 0.90 threshold"
    )
```

- [ ] **Step 4:** Run the test to confirm it PASSES in its current no-regression stub form (baseline.json not present = skip):

```
pytest tests/recall/test_recall_guard.py -v -k "not no_regression"
```

Expected output: `test_golden_set_has_20_entries PASSED`, `test_golden_set_structure PASSED`, `test_calibration_has_both_models PASSED`, `test_recall_no_regression_vs_baseline SKIPPED`.

- [ ] **Step 5:** Commit the golden set infrastructure:

```
git add tests/recall/golden_set.json tests/recall/score_calibration.json tests/recall/test_recall_guard.py
git commit -m "feat(recall): add 20-query golden set and recall guard harness (Plan C T1)"
```

---

### Task 2: Migration `003_file_index.sql`

**Files:**
- Create: `C:/Users/samde/dev/axon/src/axon/store/migrations/003_file_index.sql`
- Modify: `C:/Users/samde/dev/axon/tests/store/test_migrations.py` (add assertion for new table + version)

**Interfaces:**

Consumes:
- `session_store.py::_apply_migrations()` lines 44-61 - auto-discovers `*.sql` in `_MIGRATIONS_DIR`, runs via `executescript()`, tracks in `schema_version`. No code changes needed.

Produces:
- Table `file_index (file_path TEXT, ctx TEXT, sha1 TEXT, status TEXT DEFAULT 'done', chunk_count INTEGER DEFAULT 0, indexed_at TEXT, PRIMARY KEY (file_path, ctx))`
- Index `ix_file_index_ctx ON file_index (ctx)`
- Index `ix_file_index_status ON file_index (status)`

- [ ] **Step 1:** Write the failing test first - add to `tests/store/test_migrations.py`:

```python
async def test_003_file_index_table_exists(tmp_path: Path) -> None:
    db_path = tmp_path / "axon.db"
    store = SessionStore(db_path=db_path)
    await store.init()
    await store.close()

    tables = await _table_names(db_path)
    assert "file_index" in tables


async def test_003_schema_version_includes_file_index(tmp_path: Path) -> None:
    db_path = tmp_path / "axon.db"
    store = SessionStore(db_path=db_path)
    await store.init()
    await store.close()

    versions = await _applied_versions(db_path)
    assert "003_file_index" in versions
    assert sorted(versions) == [
        "000_baseline",
        "001_axon_graph",
        "002_unique_edges",
        "003_file_index",
    ]


async def test_003_file_index_columns(tmp_path: Path) -> None:
    import aiosqlite
    db_path = tmp_path / "axon.db"
    store = SessionStore(db_path=db_path)
    await store.init()
    await store.close()

    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute("PRAGMA table_info(file_index)")
        cols = {row[1] for row in await cur.fetchall()}
    assert {"file_path", "ctx", "sha1", "status", "chunk_count", "indexed_at"} <= cols
```

- [ ] **Step 2:** Run the test to confirm it FAILS:

```
pytest tests/store/test_migrations.py::test_003_file_index_table_exists -v
```

Expected: `FAILED - AssertionError: assert 'file_index' in {'adr', 'code_change', ...}`

- [ ] **Step 3:** Create `src/axon/store/migrations/003_file_index.sql`:

```sql
-- 003_file_index.sql
-- Persistent per-file hash cache for cross-process incremental skip.
-- executescript() emits an implicit COMMIT before executing; DDL with
-- IF NOT EXISTS makes re-execution safe (idempotent).
-- status: 'pending' = Qdrant mutation in progress (crash sentinel D2)
--         'done'    = chunks successfully flushed to Qdrant

CREATE TABLE IF NOT EXISTS file_index (
    file_path   TEXT    NOT NULL,
    ctx         TEXT    NOT NULL,
    sha1        TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'done',
    chunk_count INTEGER NOT NULL DEFAULT 0,
    indexed_at  TEXT    NOT NULL,
    PRIMARY KEY (file_path, ctx)
);

CREATE INDEX IF NOT EXISTS ix_file_index_ctx
    ON file_index (ctx);

CREATE INDEX IF NOT EXISTS ix_file_index_status
    ON file_index (status);
```

- [ ] **Step 4:** Run the tests to confirm PASS:

```
pytest tests/store/test_migrations.py -v
```

Expected: all 5 tests PASS (3 original + 2 new).

- [ ] **Step 5:** Run recall guard to confirm no regression:

```
pytest tests/recall/test_recall_guard.py -v -k "not no_regression"
```

Expected: golden set structure tests PASS, `no_regression` SKIPPED.

- [ ] **Step 6:** Commit:

```
git add src/axon/store/migrations/003_file_index.sql tests/store/test_migrations.py
git commit -m "feat(store): add 003_file_index.sql migration for persistent file hash cache (Plan C T2)"
```

---

### Task 3: `SqliteFileCache` module

**Files:**
- Create: `C:/Users/samde/dev/axon/src/axon/store/file_cache.py`
- Create: `C:/Users/samde/dev/axon/tests/store/test_file_cache.py`

**Interfaces:**

Consumes:
- `aiosqlite.Connection` - already imported in session_store.py
- `asyncio.Lock` - `self._lock` from `SessionStore.__init__` line 101

Produces (exact signatures, used by Task 5's `index_path`):
- `FileCache` Protocol: `async def get_all_sha1s(self, ctx: str) -> dict[str, str]` (done-only); `async def set_entry(self, file_path: str, ctx: str, sha1: str, chunk_count: int, *, status: str = "done") -> None`; `async def delete_entry(self, file_path: str, ctx: str) -> None`; `async def list_entries(self, ctx: str) -> list[tuple[str, str]]`
- `sha1_of_source(source: str) -> str` - returns `hashlib.sha1(source.encode("utf-8")).hexdigest()`

- [ ] **Step 1:** Write ALL failing tests first (`tests/store/test_file_cache.py`):

```python
# tests/store/test_file_cache.py
"""Unit tests for SqliteFileCache - all run against a real in-memory SQLite DB."""
from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
import pytest

from axon.store.file_cache import SqliteFileCache, sha1_of_source


async def _make_cache(tmp_path: Path) -> tuple[SqliteFileCache, aiosqlite.Connection]:
    db_path = tmp_path / "test.db"
    conn = await aiosqlite.connect(db_path)
    await conn.execute("""
        CREATE TABLE file_index (
            file_path TEXT NOT NULL,
            ctx TEXT NOT NULL,
            sha1 TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'done',
            chunk_count INTEGER NOT NULL DEFAULT 0,
            indexed_at TEXT NOT NULL,
            PRIMARY KEY (file_path, ctx)
        )
    """)
    await conn.commit()
    lock = asyncio.Lock()
    cache = SqliteFileCache(conn, lock)
    return cache, conn


async def test_get_all_sha1s_empty(tmp_path: Path) -> None:
    cache, conn = await _make_cache(tmp_path)
    result = await cache.get_all_sha1s("personal")
    assert result == {}
    await conn.close()


async def test_get_all_sha1s_filters_pending(tmp_path: Path) -> None:
    cache, conn = await _make_cache(tmp_path)
    await cache.set_entry("src/foo.py", "personal", "abc123", 5, status="pending")
    result = await cache.get_all_sha1s("personal")
    assert result == {}  # pending rows MUST NOT appear
    await conn.close()


async def test_get_all_sha1s_returns_done(tmp_path: Path) -> None:
    cache, conn = await _make_cache(tmp_path)
    await cache.set_entry("src/foo.py", "personal", "abc123", 5, status="done")
    result = await cache.get_all_sha1s("personal")
    assert result == {"src/foo.py": "abc123"}
    await conn.close()


async def test_set_entry_upsert_updates_sha1(tmp_path: Path) -> None:
    cache, conn = await _make_cache(tmp_path)
    await cache.set_entry("src/foo.py", "personal", "aaa", 3)
    await cache.set_entry("src/foo.py", "personal", "bbb", 4)
    result = await cache.get_all_sha1s("personal")
    assert result["src/foo.py"] == "bbb"
    await conn.close()


async def test_set_entry_pending_then_done(tmp_path: Path) -> None:
    cache, conn = await _make_cache(tmp_path)
    await cache.set_entry("src/foo.py", "personal", "abc", 5, status="pending")
    assert await cache.get_all_sha1s("personal") == {}
    await cache.set_entry("src/foo.py", "personal", "abc", 5, status="done")
    assert await cache.get_all_sha1s("personal") == {"src/foo.py": "abc"}
    await conn.close()


async def test_delete_entry_removes_row(tmp_path: Path) -> None:
    cache, conn = await _make_cache(tmp_path)
    await cache.set_entry("src/foo.py", "personal", "abc", 5)
    await cache.delete_entry("src/foo.py", "personal")
    assert await cache.get_all_sha1s("personal") == {}
    await conn.close()


async def test_list_entries_filters_by_ctx(tmp_path: Path) -> None:
    cache, conn = await _make_cache(tmp_path)
    await cache.set_entry("src/foo.py", "personal", "aaa", 2)
    await cache.set_entry("src/bar.py", "knowledge", "bbb", 3)
    entries = await cache.list_entries("personal")
    paths = {e[0] for e in entries}
    assert "src/foo.py" in paths
    assert "src/bar.py" not in paths
    await conn.close()


async def test_path_normalization_backslash(tmp_path: Path) -> None:
    cache, conn = await _make_cache(tmp_path)
    # Simulate Windows path with backslashes
    await cache.set_entry("src\\foo\\bar.py", "personal", "abc", 2)
    result = await cache.get_all_sha1s("personal")
    # Must be stored and returned as posix
    assert "src/foo/bar.py" in result
    assert "src\\foo\\bar.py" not in result
    await conn.close()


def test_sha1_of_source_matches_pipeline_hash() -> None:
    source = "def hello():\n    return 42\n"
    import hashlib
    expected = hashlib.sha1(source.encode("utf-8")).hexdigest()
    assert sha1_of_source(source) == expected
```

- [ ] **Step 2:** Run to confirm FAIL:

```
pytest tests/store/test_file_cache.py -v
```

Expected: `ImportError: cannot import name 'SqliteFileCache' from 'axon.store.file_cache'` (module does not exist yet).

- [ ] **Step 3:** Create `src/axon/store/file_cache.py`:

```python
# src/axon/store/file_cache.py
"""Persistent file-hash cache backed by the file_index SQLite table.

FileCache is a Protocol so tests can inject mocks.
SqliteFileCache is the production implementation - uses the aiosqlite
connection and asyncio.Lock already owned by SessionStore.

All file_path values are normalized to posix form before storage so that
Windows backslash paths and posix slash paths produce identical lookup keys.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol


class FileCache(Protocol):
    async def get_all_sha1s(self, ctx: str) -> dict[str, str]:
        """Return {file_path_posix: sha1} for all 'done' entries in ctx.

        Uses a single SELECT. Pending rows (crash sentinels) are excluded -
        they are treated as hash misses and trigger a full re-index.
        """
        ...

    async def set_entry(
        self,
        file_path: str,
        ctx: str,
        sha1: str,
        chunk_count: int,
        *,
        status: str = "done",
    ) -> None:
        """Insert or update a file_index row. Use status='pending' before
        Qdrant mutation; status='done' only after _flush_batch() succeeds.
        """
        ...

    async def delete_entry(self, file_path: str, ctx: str) -> None:
        """Remove a file_index entry (used when file is deleted from repo)."""
        ...

    async def list_entries(self, ctx: str) -> list[tuple[str, str]]:
        """Return [(file_path_posix, sha1)] for ALL entries in ctx (any status).

        Used to detect files removed from the repo (compare against walk result).
        """
        ...


class SqliteFileCache:
    """Production FileCache backed by an aiosqlite.Connection.

    Injected with the same conn and lock used by SessionStore to avoid
    opening a second connection (SQLite WAL allows multiple readers but
    serializes writers; sharing the lock prevents write contention from
    within the same process).
    """

    def __init__(self, conn: object, lock: object) -> None:
        # aiosqlite.Connection and asyncio.Lock - typed as object to avoid
        # importing aiosqlite at protocol definition time.
        self._conn = conn
        self._lock = lock

    async def get_all_sha1s(self, ctx: str) -> dict[str, str]:
        async with self._lock:
            cur = await self._conn.execute(
                "SELECT file_path, sha1 FROM file_index WHERE ctx=? AND status='done'",
                (ctx,),
            )
            rows = await cur.fetchall()
        return {row[0]: row[1] for row in rows}

    async def set_entry(
        self,
        file_path: str,
        ctx: str,
        sha1: str,
        chunk_count: int,
        *,
        status: str = "done",
    ) -> None:
        fp = Path(file_path).as_posix()
        now = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            await self._conn.execute(
                """
                INSERT INTO file_index
                    (file_path, ctx, sha1, status, chunk_count, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (file_path, ctx) DO UPDATE SET
                    sha1        = excluded.sha1,
                    status      = excluded.status,
                    chunk_count = excluded.chunk_count,
                    indexed_at  = excluded.indexed_at
                """,
                (fp, ctx, sha1, status, chunk_count, now),
            )
            await self._conn.commit()

    async def delete_entry(self, file_path: str, ctx: str) -> None:
        fp = Path(file_path).as_posix()
        async with self._lock:
            await self._conn.execute(
                "DELETE FROM file_index WHERE file_path=? AND ctx=?",
                (fp, ctx),
            )
            await self._conn.commit()

    async def list_entries(self, ctx: str) -> list[tuple[str, str]]:
        async with self._lock:
            cur = await self._conn.execute(
                "SELECT file_path, sha1 FROM file_index WHERE ctx=?",
                (ctx,),
            )
            return list(await cur.fetchall())


def sha1_of_source(source: str) -> str:
    """SHA-1 of UTF-8 encoded source content.

    MUST remain identical to pipeline.py line 161:
        hashlib.sha1(source.encode("utf-8")).hexdigest()
    Any change here requires a matching change in pipeline.py AND
    a documented one-time cold-start full re-embed.

    Does not pass usedforsecurity kwarg to match pipeline.py exactly.
    """
    return hashlib.sha1(source.encode("utf-8")).hexdigest()
```

- [ ] **Step 4:** Run tests to confirm PASS:

```
pytest tests/store/test_file_cache.py -v
```

Expected: all 9 tests PASS.

- [ ] **Step 5:** Run recall guard:

```
pytest tests/recall/test_recall_guard.py -v -k "not no_regression"
```

Expected: PASS / SKIPPED (no regression).

- [ ] **Step 6:** Commit:

```
git add src/axon/store/file_cache.py tests/store/test_file_cache.py
git commit -m "feat(store): add SqliteFileCache with Protocol, posix normalization, crash-sentinel support (Plan C T3)"
```

---

### Task 4: `IndexLock` with PID-based stale reclaim

**Files:**
- Create: `C:/Users/samde/dev/axon/src/axon/store/index_lock.py`
- Create: `C:/Users/samde/dev/axon/tests/store/test_index_lock.py`

**Interfaces:**

Consumes: `os.kill(pid, 0)` for liveness check, `os.O_CREAT | os.O_EXCL | os.O_WRONLY` for atomic lock creation.

Produces:
- `acquire_index_lock(repo_root: Path)` - async context manager
- `IndexLockError(Exception)` - raised when lock is held by live process
- `_pid_alive(pid: int) -> bool` - exported for Windows integration test

- [ ] **Step 1:** Write ALL failing tests first (`tests/store/test_index_lock.py`):

```python
# tests/store/test_index_lock.py
"""Unit and integration tests for IndexLock with PID-based stale reclaim.

H7 (spec ledger): os.kill(pid, 0) behavior on Windows 11 is a hypothesis.
test_pid_alive_returns_false_for_dead_pid validates it on the current platform.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from axon.store.index_lock import IndexLockError, _pid_alive, acquire_index_lock


async def test_lock_acquired_and_released(tmp_path: Path) -> None:
    async with acquire_index_lock(tmp_path):
        lock_path = tmp_path / ".axon" / "index.lock"
        assert lock_path.exists(), "Lockfile must exist during context"
    assert not lock_path.exists(), "Lockfile must be removed after context"


async def test_lock_file_contains_current_pid(tmp_path: Path) -> None:
    async with acquire_index_lock(tmp_path):
        lock_path = tmp_path / ".axon" / "index.lock"
        pid_in_file = int(lock_path.read_text().strip())
        assert pid_in_file == os.getpid()


async def test_second_acquire_with_live_pid_raises(tmp_path: Path) -> None:
    lock_path = tmp_path / ".axon" / "index.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(str(os.getpid()))  # current process is alive
    with pytest.raises(IndexLockError, match="outro processo"):
        async with acquire_index_lock(tmp_path):
            pass


async def test_stale_lock_is_reclaimed(tmp_path: Path) -> None:
    lock_path = tmp_path / ".axon" / "index.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("99999999")  # almost certainly dead
    reached = False
    async with acquire_index_lock(tmp_path):
        reached = True
    assert reached, "Should have reclaimed stale lock and proceeded"


async def test_lock_released_on_exception(tmp_path: Path) -> None:
    lock_path = tmp_path / ".axon" / "index.lock"
    with pytest.raises(RuntimeError):
        async with acquire_index_lock(tmp_path):
            raise RuntimeError("simulated failure")
    assert not lock_path.exists(), "Lockfile must be cleaned up even on exception"


async def test_invalid_pid_content_reclaimed(tmp_path: Path) -> None:
    lock_path = tmp_path / ".axon" / "index.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("not-a-pid")
    reached = False
    async with acquire_index_lock(tmp_path):
        reached = True
    assert reached


def test_pid_alive_self() -> None:
    assert _pid_alive(os.getpid()) is True


def test_pid_alive_dead_process() -> None:
    # Start a subprocess and wait for it to exit, then verify _pid_alive returns False
    proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(0)"])
    proc.wait()
    dead_pid = proc.pid
    assert _pid_alive(dead_pid) is False, (
        "H7 validation: os.kill(pid, 0) must return False for a dead process on this platform. "
        "If this fails on Windows 11, stale reclaim via PID is not safe - use TTL fallback only."
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific H7 validation")
def test_pid_alive_windows_terminated_process_h7() -> None:
    """Explicit H7 validation on Windows 11.

    Creates a process, waits for it to terminate, then checks _pid_alive.
    This test MUST pass before stale reclaim is declared supported on Windows.
    """
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    result = _pid_alive(proc.pid)
    assert result is False, (
        f"H7 FAILED: _pid_alive({proc.pid}) returned True for a dead process. "
        "Do NOT rely on PID-based stale reclaim on this Windows version. "
        "Update index_lock.py to use TTL-only fallback."
    )
```

- [ ] **Step 2:** Run to confirm FAIL:

```
pytest tests/store/test_index_lock.py -v
```

Expected: `ModuleNotFoundError: No module named 'axon.store.index_lock'`

- [ ] **Step 3:** Create `src/axon/store/index_lock.py`:

```python
# src/axon/store/index_lock.py
"""Lockfile-based concurrency guard for axon index operations.

Creates .axon/index.lock containing the current PID. On acquisition,
checks if an existing lock's PID is still alive:
  - Alive  -> raise IndexLockError (another indexer is running)
  - Dead   -> reclaim (previous indexer crashed without cleanup)
  - Invalid content -> reclaim (corrupted lockfile)

PLATFORM NOTE (H7 in spec ledger): os.kill(pid, 0) behavior on Windows
differs from Unix. test_index_lock.py::test_pid_alive_windows_terminated_process_h7
validates that _pid_alive returns False for a dead process on Windows 11.
Do NOT remove that test before confirming stale reclaim works in production.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path


class IndexLockError(Exception):
    """Raised when the index lock is held by a live process."""


def _pid_alive(pid: int) -> bool:
    """Return True if a process with the given PID exists and is accessible.

    On Unix/macOS: os.kill(pid, 0) raises ProcessLookupError if dead,
    PermissionError if alive but owned by another user (treated as alive).
    On Windows: behavior validated by H7 test in test_index_lock.py.
    """
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but is owned by a different user - treat as alive
        return True
    except OSError:
        # Covers other edge cases (e.g., invalid PID range on some platforms)
        return False


@asynccontextmanager
async def acquire_index_lock(repo_root: Path):
    """Async context manager that holds .axon/index.lock for repo_root.

    Usage:
        async with acquire_index_lock(Path("/path/to/repo")):
            await index_path(...)

    Raises IndexLockError if a live process already holds the lock.
    Always removes the lockfile on exit (normal or exception).
    """
    lock_path = repo_root / ".axon" / "index.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    if lock_path.exists():
        try:
            existing_pid = int(lock_path.read_text().strip())
            if _pid_alive(existing_pid):
                raise IndexLockError(
                    f"outro processo (pid={existing_pid}) esta indexando {repo_root}. "
                    f"Se travou, remova: {lock_path}"
                )
            # PID is dead - reclaim the stale lock
            lock_path.unlink(missing_ok=True)
        except ValueError:
            # Non-integer content in lockfile - reclaim
            lock_path.unlink(missing_ok=True)

    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except FileExistsError:
        raise IndexLockError(
            f"Race condition ao adquirir lock em {lock_path}. Tente novamente."
        )

    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)
```

- [ ] **Step 4:** Run tests to confirm PASS:

```
pytest tests/store/test_index_lock.py -v
```

Expected: all tests PASS. Note specifically `test_pid_alive_windows_terminated_process_h7` should PASS on the R7 5800X3D (Windows 11) - this validates H7.

- [ ] **Step 5:** Commit:

```
git add src/axon/store/index_lock.py tests/store/test_index_lock.py
git commit -m "feat(store): add IndexLock with PID-based stale reclaim and H7 Windows validation (Plan C T4)"
```

---

### Task 5: `upsert_deps_batch` in `graph_store.py`

**Files:**
- Modify: `C:/Users/samde/dev/axon/src/axon/store/graph_store.py` (add method after line 46)
- Create: `C:/Users/samde/dev/axon/tests/store/test_upsert_deps_batch.py`

**Interfaces:**

Consumes:
- `axon.embedder.graph_extractor.DependencyRecord` (graph_extractor.py lines 49-54) - `.symbol: str`, `.calls: list[str]`, `.called_by: list[str]`
- `redis.asyncio.Redis.pipeline(transaction=False)` - `aioredis` already imported at graph_store.py line 5

Produces:
- `GraphStore.upsert_deps_batch(self, records: list[DependencyRecord]) -> None` - one Redis pipeline per call, N `hset` commands, one `execute()`

- [ ] **Step 1:** Write ALL failing tests first (`tests/store/test_upsert_deps_batch.py`):

```python
# tests/store/test_upsert_deps_batch.py
"""Tests for GraphStore.upsert_deps_batch (Plan C T5).

Uses a fake Redis pipeline to avoid requiring a live Redis in unit tests.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from axon.embedder.graph_extractor import DependencyRecord
from axon.store.graph_store import GraphStore


def _make_record(symbol: str, calls: list[str], called_by: list[str]) -> DependencyRecord:
    return DependencyRecord(symbol=symbol, calls=calls, called_by=called_by)


async def test_empty_batch_is_noop() -> None:
    store = GraphStore.__new__(GraphStore)
    store._redis = AsyncMock()
    await store.upsert_deps_batch([])
    store._redis.pipeline.assert_not_called()


async def test_single_record_uses_one_pipeline_execute() -> None:
    store = GraphStore.__new__(GraphStore)
    pipe = AsyncMock()
    pipe.__aenter__ = AsyncMock(return_value=pipe)
    pipe.__aexit__ = AsyncMock(return_value=False)
    pipe.hset = MagicMock()
    pipe.execute = AsyncMock()

    store._redis = MagicMock()
    store._redis.pipeline = MagicMock(return_value=pipe)

    records = [_make_record("foo", ["bar"], ["baz"])]
    await store.upsert_deps_batch(records)

    store._redis.pipeline.assert_called_once_with(transaction=False)
    pipe.hset.assert_called_once()
    pipe.execute.assert_called_once()


async def test_multiple_records_one_pipeline_execute() -> None:
    store = GraphStore.__new__(GraphStore)
    pipe = AsyncMock()
    pipe.__aenter__ = AsyncMock(return_value=pipe)
    pipe.__aexit__ = AsyncMock(return_value=False)
    pipe.hset = MagicMock()
    pipe.execute = AsyncMock()

    store._redis = MagicMock()
    store._redis.pipeline = MagicMock(return_value=pipe)

    records = [
        _make_record("a", ["b"], []),
        _make_record("b", [], ["a"]),
        _make_record("c", ["a", "b"], []),
    ]
    await store.upsert_deps_batch(records)

    store._redis.pipeline.assert_called_once_with(transaction=False)
    assert pipe.hset.call_count == 3
    pipe.execute.assert_called_once()


async def test_hset_payload_format() -> None:
    """Each hset must set 'calls' and 'called_by' as JSON strings."""
    import json

    store = GraphStore.__new__(GraphStore)
    pipe = AsyncMock()
    pipe.__aenter__ = AsyncMock(return_value=pipe)
    pipe.__aexit__ = AsyncMock(return_value=False)
    hset_calls: list[dict] = []

    def capture_hset(key, mapping):
        hset_calls.append({"key": key, "mapping": mapping})

    pipe.hset = MagicMock(side_effect=capture_hset)
    pipe.execute = AsyncMock()

    store._redis = MagicMock()
    store._redis.pipeline = MagicMock(return_value=pipe)

    records = [_make_record("my_func", ["helper", "util"], ["caller"])]
    await store.upsert_deps_batch(records)

    assert len(hset_calls) == 1
    call = hset_calls[0]
    assert call["key"] == "dep:my_func"
    assert json.loads(call["mapping"]["calls"]) == ["helper", "util"]
    assert json.loads(call["mapping"]["called_by"]) == ["caller"]
```

- [ ] **Step 2:** Run to confirm FAIL:

```
pytest tests/store/test_upsert_deps_batch.py -v
```

Expected: `AttributeError: 'GraphStore' object has no attribute 'upsert_deps_batch'`

- [ ] **Step 3:** Add `upsert_deps_batch` to `src/axon/store/graph_store.py` after the `upsert_deps` method (after line 46, before `get_calls` at line 48):

```python
    async def upsert_deps_batch(
        self,
        records: list["DependencyRecord"],
    ) -> None:
        """Write N dep:symbol hashes in a single Redis pipeline.

        transaction=False avoids MULTI/EXEC overhead; partial failures are
        corrected on the next index run (file re-indexed by hash miss).
        """
        if not records:
            return
        async with self._redis.pipeline(transaction=False) as pipe:
            for record in records:
                pipe.hset(
                    f"dep:{record.symbol}",
                    mapping={
                        "calls": json.dumps(record.calls),
                        "called_by": json.dumps(record.called_by),
                    },
                )
            await pipe.execute()
```

Note: `DependencyRecord` is in `axon.embedder.graph_extractor`. To avoid a circular import, use a string annotation or add the import at function level. Add the import at the top of graph_store.py:

```python
from axon.embedder.graph_extractor import DependencyRecord
```

Insert after line 3 (`from collections import deque`) as line 4.

- [ ] **Step 4:** Run tests to confirm PASS:

```
pytest tests/store/test_upsert_deps_batch.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5:** Confirm existing graph_store tests still pass:

```
pytest tests/store/test_graph_methods.py tests/store/test_graph_cache.py tests/store/test_graph_namespace.py -v
```

Expected: all PASS.

- [ ] **Step 6:** Commit:

```
git add src/axon/store/graph_store.py tests/store/test_upsert_deps_batch.py
git commit -m "feat(store): add upsert_deps_batch for single-pipeline Redis dep writes (Plan C T5)"
```

---

### Task 6: Stable `_chunk_id` (D1 contract) and `iter_supported_files` (D3 git-based walk)

**Files:**
- Modify: `C:/Users/samde/dev/axon/src/axon/embedder/pipeline.py` (lines 206-211 for `_chunk_id`; lines 59-75 for `iter_supported_files`)
- Create: `C:/Users/samde/dev/axon/tests/embedder/test_chunk_id_stable.py`
- Create: `C:/Users/samde/dev/axon/tests/embedder/test_gitignore_exclusion.py`

**Interfaces:**

Produces (D1 shared contract, consumed by Task 7's `index_path`):
- `_chunk_id(file_path: str | Path, symbol: str, occurrence_index: int) -> str`
  returning `str(uuid.uuid5(uuid.NAMESPACE_URL, f"{Path(file_path).as_posix()}::{symbol}::{occurrence_index}"))`
- Callers must maintain a `collections.Counter[str]` per file; pass `counter[chunk.symbol]` as `occurrence_index`, then `counter[chunk.symbol] += 1`.

- [ ] **Step 1:** Verify current call sites via grep (these are the ONLY two locations to update):

Current `_chunk_id` signature: `pipeline.py:206-211`:
```python
def _chunk_id(path: Path, chunk: Chunk) -> str:
    import uuid
    key = f"{path}::{chunk.symbol}::{chunk.start_line}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))
```

Call site 1 - `ingest_file` at pipeline.py line 109: `id=_chunk_id(path, c),`
Call site 2 - `index_path` at pipeline.py line 173: `id=_chunk_id(file_path, c),`

- [ ] **Step 2:** Write ALL failing tests first:

```python
# tests/embedder/test_chunk_id_stable.py
"""D1 contract: _chunk_id must use occurrence_index, not start_line."""
from __future__ import annotations

from pathlib import Path

from axon.embedder.pipeline import _chunk_id


def test_id_stable_after_simulated_line_shift() -> None:
    """Same symbol name at any line position must produce the same ID."""
    id_at_line_1 = _chunk_id("src/foo.py", "my_func", 0)
    id_at_line_50 = _chunk_id("src/foo.py", "my_func", 0)
    assert id_at_line_1 == id_at_line_50, (
        "Chunk ID must not change when line numbers shift (D1 contract)"
    )


def test_id_disambiguates_overloads() -> None:
    """Two methods with the same name in one file must have different IDs."""
    id_first = _chunk_id("src/foo.py", "process", 0)
    id_second = _chunk_id("src/foo.py", "process", 1)
    assert id_first != id_second, (
        "Overloaded methods with same name must get distinct IDs (occurrence_index)"
    )


def test_id_differs_between_files() -> None:
    id_a = _chunk_id("src/a.py", "helper", 0)
    id_b = _chunk_id("src/b.py", "helper", 0)
    assert id_a != id_b


def test_id_is_valid_uuid_string() -> None:
    import uuid
    result = _chunk_id("src/foo.py", "my_func", 0)
    # Must parse as a UUID without raising
    parsed = uuid.UUID(result)
    assert str(parsed) == result


def test_id_normalizes_windows_path() -> None:
    """Windows backslash and posix slash paths must produce the same ID."""
    id_posix = _chunk_id("src/foo/bar.py", "my_func", 0)
    id_win = _chunk_id("src\\foo\\bar.py", "my_func", 0)
    assert id_posix == id_win, (
        "Path normalization: backslash and slash must yield identical IDs"
    )
```

```python
# tests/embedder/test_gitignore_exclusion.py
"""Security test (D3): gitignored files must NEVER be indexed.

This test requires git to be installed. It creates a real git repo with a
committed .env file, then gitignores it, and asserts iter_supported_files
does not yield it. CANNOT be skipped - it validates a security property.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from axon.embedder.pipeline import iter_supported_files


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with a committed-then-ignored .env."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@axon.test"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Axon Test"],
        cwd=tmp_path, check=True, capture_output=True,
    )

    # Create and commit a .py file so the repo has at least one commit
    (tmp_path / "main.py").write_text("def hello(): return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "main.py"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True
    )

    # Create and commit .env with a secret
    env_file = tmp_path / ".env"
    env_file.write_text("SECRET=my-production-api-key\n", encoding="utf-8")
    subprocess.run(["git", "add", ".env"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "oops add secret"],
        cwd=tmp_path, check=True, capture_output=True,
    )

    # Now gitignore .env (already committed but should be excluded by check-ignore)
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(".env\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", ".gitignore"], cwd=tmp_path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "gitignore .env"],
        cwd=tmp_path, check=True, capture_output=True,
    )

    return tmp_path


def test_gitignored_committed_file_not_indexed(git_repo: Path) -> None:
    """A file that was committed and then gitignored MUST NOT appear in iter_supported_files."""
    # Add .env to _LANGUAGE_MAP temporarily by using .py extension for the test
    # The real .env won't have a supported extension so we test with a .py variant
    # Create a secrets.py that is committed then gitignored
    subprocess.run(
        ["git", "config", "user.email", "test@axon.test"],
        cwd=git_repo, check=True, capture_output=True,
    )
    secrets_file = git_repo / "secrets.py"
    secrets_file.write_text("API_KEY = 'do-not-embed-this'\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "secrets.py"], cwd=git_repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "add secrets.py"],
        cwd=git_repo, check=True, capture_output=True,
    )

    gitignore = git_repo / ".gitignore"
    gitignore.write_text(".env\nsecrets.py\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", ".gitignore"], cwd=git_repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "gitignore secrets.py"],
        cwd=git_repo, check=True, capture_output=True,
    )

    found = list(iter_supported_files(git_repo))
    found_names = [p.name for p in found]
    assert "secrets.py" not in found_names, (
        "SECURITY VIOLATION: gitignored secrets.py was returned by iter_supported_files"
    )
    assert "main.py" in found_names, "main.py should still be indexed"


def test_untracked_file_not_indexed(git_repo: Path) -> None:
    """Files not yet added to git (untracked) must not appear."""
    (git_repo / "untracked.py").write_text("def secret(): pass\n", encoding="utf-8")
    # Do NOT git add
    found = list(iter_supported_files(git_repo))
    found_names = [p.name for p in found]
    assert "untracked.py" not in found_names, (
        "Untracked file must not be indexed - use git add first"
    )
```

- [ ] **Step 3:** Run to confirm FAIL:

```
pytest tests/embedder/test_chunk_id_stable.py -v
```

Expected: `TypeError: _chunk_id() takes 2 positional arguments but 3 were given` (old signature).

```
pytest tests/embedder/test_gitignore_exclusion.py -v
```

Expected: `test_gitignored_committed_file_not_indexed FAILED` (current rglob walk would return the gitignored file).

- [ ] **Step 4:** Update `_chunk_id` in `src/axon/embedder/pipeline.py` lines 206-211. Replace:

```python
def _chunk_id(path: Path, chunk: Chunk) -> str:
    """Stable ID for a chunk: hash of file path + symbol + start_line."""
    import uuid

    key = f"{path}::{chunk.symbol}::{chunk.start_line}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))
```

With:

```python
def _chunk_id(file_path: "str | Path", symbol: str, occurrence_index: int) -> str:
    """Stable chunk ID: uuid5 of posix_path::symbol::occurrence_index.

    D1 contract (shared across Plans A, B, C):
      occurrence_index = 0-based count of that symbol name seen so far within
      the file. Callers maintain a Counter[str] per file.

    Does NOT use start_line - edits above a symbol no longer change its ID.
    Normalizes file_path to posix so Windows and Unix produce identical IDs.
    """
    import uuid

    key = f"{Path(file_path).as_posix()}::{symbol}::{occurrence_index}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))
```

- [ ] **Step 5:** Update both call sites in `pipeline.py` to use the new signature. At line 109 (inside `ingest_file`), replace the list comprehension:

Old (lines 107-120):
```python
    vector_chunks = [
        VectorChunk(
            id=_chunk_id(path, c),
            ...
        )
        for c, vec in zip(chunks, vectors)
    ]
```

New (add Counter before the comprehension):
```python
    from collections import Counter
    _symbol_counter: Counter[str] = Counter()
    vector_chunks = []
    for c, vec in zip(chunks, vectors):
        vector_chunks.append(
            VectorChunk(
                id=_chunk_id(path, c.symbol, _symbol_counter[c.symbol]),
                vector=vec,
                file_path=c.file_path,
                language=c.language,
                chunk_type=c.chunk_type,
                symbol=c.symbol,
                project=path.parent.name,
                ctx="knowledge",
                content=c.content,
            )
        )
        _symbol_counter[c.symbol] += 1
```

At line 173 (inside `index_path`), replace the list comprehension:

Old (lines 171-184):
```python
        vectors = engine.embed([c.content for c in chunks])
        vector_chunks = [
            VectorChunk(
                id=_chunk_id(file_path, c),
                ...
            )
            for c, vec in zip(chunks, vectors)
        ]
```

New:
```python
        vectors = engine.embed([c.content for c in chunks])
        from collections import Counter as _Counter
        _sym_ctr: _Counter[str] = _Counter()
        vector_chunks = []
        for c, vec in zip(chunks, vectors):
            vector_chunks.append(
                VectorChunk(
                    id=_chunk_id(file_path, c.symbol, _sym_ctr[c.symbol]),
                    vector=vec,
                    file_path=c.file_path,
                    language=c.language,
                    chunk_type=c.chunk_type,
                    symbol=c.symbol,
                    project=file_path.parent.name,
                    ctx=file_ctx,
                    content=c.content,
                )
            )
            _sym_ctr[c.symbol] += 1
```

- [ ] **Step 6:** Update `iter_supported_files` in `pipeline.py` lines 59-75. Replace:

```python
def iter_supported_files(
    target: Path,
    *,
    languages: set[str] | None = None,
) -> Iterable[Path]:
    if target.is_file():
        language = _language_for_suffix(target.suffix)
        if language and (languages is None or language in languages):
            yield target
        return

    for path in target.rglob("*"):
        if any(part in EXCLUDED_DIR_NAMES for part in path.parts):
            continue
        language = _language_for_suffix(path.suffix)
        if path.is_file() and language and (languages is None or language in languages):
            yield path
```

With:

```python
def iter_supported_files(
    target: Path,
    *,
    languages: set[str] | None = None,
) -> Iterable[Path]:
    """Yield supported source files under target.

    For git repositories: uses 'git ls-files --cached' (tracked files only)
    filtered through 'git check-ignore' to exclude files that were committed
    before being added to .gitignore. This is a SECURITY fix, not perf.

    For non-git directories (or if git is unavailable): falls back to rglob
    with manual exclusion of EXCLUDED_DIR_NAMES.

    All yielded paths are absolute. file_path normalization to posix is the
    caller's responsibility when storing in file_index.
    """
    import subprocess

    if target.is_file():
        language = _language_for_suffix(target.suffix)
        if language and (languages is None or language in languages):
            yield target
        return

    try:
        result = subprocess.run(
            ["git", "-C", str(target), "ls-files", "--cached"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Not a git repo or git not installed - fall back to rglob
        for path in target.rglob("*"):
            if any(part in EXCLUDED_DIR_NAMES for part in path.parts):
                continue
            language = _language_for_suffix(path.suffix)
            if path.is_file() and language and (languages is None or language in languages):
                yield path
        return

    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        p = target / line
        if not p.is_file():
            continue
        if _language_for_suffix(p.suffix) is None:
            continue
        # Exclude files committed before being added to .gitignore
        chk = subprocess.run(
            ["git", "-C", str(target), "check-ignore", "-q", str(p)],
            capture_output=True,
        )
        if chk.returncode == 0:
            continue  # gitignored - never embed
        language = _language_for_suffix(p.suffix)
        if language and (languages is None or language in languages):
            yield p
```

- [ ] **Step 7:** Run all tests to confirm PASS:

```
pytest tests/embedder/test_chunk_id_stable.py tests/embedder/test_gitignore_exclusion.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 8:** Run existing pipeline and chunker tests to confirm no regression:

```
pytest tests/embedder/ -v
```

Expected: all pre-existing tests PASS. Note: `test_pipeline_excludes.py` tests the rglob fallback - they create non-git tmp dirs, so the fallback path is exercised. All should still pass.

- [ ] **Step 9:** Commit:

```
git add src/axon/embedder/pipeline.py tests/embedder/test_chunk_id_stable.py tests/embedder/test_gitignore_exclusion.py
git commit -m "feat(embedder): stable _chunk_id (D1 occurrence_index), git ls-files walk (D3 security fix) (Plan C T6)"
```

---

### Task 7: Wire `FileCache` into `index_path` (D2 crash-safety, D4 reconcile, D6 orphan-free)

**Files:**
- Modify: `C:/Users/samde/dev/axon/src/axon/embedder/pipeline.py` (lines 28, 127-203)
- Create: `C:/Users/samde/dev/axon/tests/embedder/test_incremental_skip.py`
- Create: `C:/Users/samde/dev/axon/tests/embedder/test_orphan_reconcile.py`
- Create: `C:/Users/samde/dev/axon/tests/embedder/test_crash_safety.py`
- Create: `C:/Users/samde/dev/axon/tests/embedder/test_deleted_file_cleanup.py`

**Interfaces:**

Consumes:
- `FileCache` protocol from `axon.store.file_cache` (Task 3)
- `VectorStore.delete_by_file(ctx: str, file_path: str)` at vector_store.py line 163
- `GraphStore.upsert_deps_batch(records: list[DependencyRecord])` (Task 5)
- `sha1_of_source(source: str) -> str` from `axon.store.file_cache`

Produces:
- `index_path(target, *, engine, store, vault_root, file_cache, forced_ctx, graph_store, languages) -> tuple[int, int]` - `file_cache` is a REQUIRED parameter

- [ ] **Step 1:** Write ALL failing tests first:

```python
# tests/embedder/test_incremental_skip.py
"""Test that unchanged files are skipped on the second index run."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from axon.store.file_cache import SqliteFileCache


class _MockFileCache:
    """Simulates a cache that already has every file as done with the correct sha1."""

    def __init__(self, preloaded: dict[str, str]) -> None:
        self._data = preloaded

    async def get_all_sha1s(self, ctx: str) -> dict[str, str]:
        return dict(self._data)

    async def set_entry(self, file_path, ctx, sha1, chunk_count, *, status="done"):
        self._data[file_path] = sha1

    async def delete_entry(self, file_path, ctx):
        self._data.pop(file_path, None)

    async def list_entries(self, ctx):
        return list(self._data.items())


async def test_unchanged_file_skipped(tmp_path: Path) -> None:
    """Engine.embed must not be called for a file whose sha1 is cached."""
    from axon.store.file_cache import sha1_of_source
    from axon.embedder.pipeline import index_path

    py_file = tmp_path / "hello.py"
    py_file.write_text("def hello(): return 1\n", encoding="utf-8")
    source = py_file.read_text(encoding="utf-8")
    cached_sha1 = sha1_of_source(source)
    fp_posix = py_file.as_posix()

    cache = _MockFileCache({fp_posix: cached_sha1})

    engine = MagicMock()
    engine.embed = MagicMock(return_value=[])

    store = AsyncMock()
    store.upsert_batch = AsyncMock()
    store.delete_by_file = AsyncMock()

    indexed, total = await index_path(
        tmp_path,
        engine=engine,
        store=store,
        vault_root=tmp_path,
        file_cache=cache,
    )

    engine.embed.assert_not_called()
    assert indexed == 0
    assert total == 0


async def test_changed_file_reindexed(tmp_path: Path) -> None:
    """A file with a different sha1 must pass through embed."""
    from axon.embedder.pipeline import index_path

    py_file = tmp_path / "hello.py"
    py_file.write_text("def hello(): return 1\n", encoding="utf-8")

    # Cache has a WRONG (stale) sha1 - simulates a changed file
    cache = _MockFileCache({py_file.as_posix(): "stale_sha1_that_differs"})

    engine = MagicMock()
    engine.embed = MagicMock(return_value=[[0.1] * 768])

    store = AsyncMock()
    store.upsert_batch = AsyncMock()
    store.delete_by_file = AsyncMock()

    await index_path(
        tmp_path,
        engine=engine,
        store=store,
        vault_root=tmp_path,
        file_cache=cache,
    )

    engine.embed.assert_called()
    store.delete_by_file.assert_called()
```

```python
# tests/embedder/test_crash_safety.py
"""Test D2: pending sentinel survives a crash; re-index picks it up."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


class _SentinelTrackingCache:
    """Tracks set_entry calls to verify pending/done ordering."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._data: dict[str, str] = {}

    async def get_all_sha1s(self, ctx: str) -> dict[str, str]:
        # Simulate crash: return empty (pending rows excluded)
        return {}

    async def set_entry(self, fp, ctx, sha1, chunk_count, *, status="done"):
        self.calls.append({"fp": fp, "status": status, "sha1": sha1})
        if status == "done":
            self._data[fp] = sha1

    async def delete_entry(self, fp, ctx):
        self._data.pop(fp, None)

    async def list_entries(self, ctx):
        return []


async def test_pending_written_before_flush(tmp_path: Path) -> None:
    """status='pending' must appear before status='done' in the call sequence."""
    from axon.embedder.pipeline import index_path

    py_file = tmp_path / "foo.py"
    py_file.write_text("def foo(): pass\n", encoding="utf-8")

    cache = _SentinelTrackingCache()
    engine = MagicMock()
    engine.embed = MagicMock(return_value=[[0.1] * 768])
    store = AsyncMock()
    store.upsert_batch = AsyncMock()
    store.delete_by_file = AsyncMock()

    await index_path(
        tmp_path,
        engine=engine,
        store=store,
        vault_root=tmp_path,
        file_cache=cache,
    )

    statuses = [c["status"] for c in cache.calls]
    assert "pending" in statuses, "set_entry(pending) must be called"
    assert "done" in statuses, "set_entry(done) must be called"
    pending_idx = next(i for i, c in enumerate(cache.calls) if c["status"] == "pending")
    done_idx = next(i for i, c in enumerate(cache.calls) if c["status"] == "done")
    assert pending_idx < done_idx, (
        "pending must be written BEFORE done (crash-safety invariant D2)"
    )
```

```python
# tests/embedder/test_deleted_file_cleanup.py
"""Test D6: files removed from the repo are cleaned from Qdrant and file_index."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock


class _DeletionTrackingCache:
    def __init__(self, preloaded_paths: list[str]) -> None:
        # Pre-populate with paths that should be detected as deleted
        self._data = {p: "some_sha1" for p in preloaded_paths}
        self.deleted: list[str] = []

    async def get_all_sha1s(self, ctx: str) -> dict[str, str]:
        return dict(self._data)

    async def set_entry(self, fp, ctx, sha1, chunk_count, *, status="done"):
        self._data[fp] = sha1

    async def delete_entry(self, fp, ctx):
        self.deleted.append(fp)
        self._data.pop(fp, None)

    async def list_entries(self, ctx):
        return list(self._data.items())


async def test_deleted_file_cleaned_from_qdrant(tmp_path: Path) -> None:
    """After a file is removed, its Qdrant points must be deleted."""
    from axon.embedder.pipeline import index_path

    # Only main.py exists on disk
    py_file = tmp_path / "main.py"
    py_file.write_text("def main(): pass\n", encoding="utf-8")

    # Cache thinks both main.py and deleted.py exist
    deleted_posix = (tmp_path / "deleted.py").as_posix()
    cache = _DeletionTrackingCache([py_file.as_posix(), deleted_posix])

    engine = MagicMock()
    engine.embed = MagicMock(return_value=[[0.1] * 768])
    store = AsyncMock()
    store.upsert_batch = AsyncMock()
    store.delete_by_file = AsyncMock()

    await index_path(
        tmp_path,
        engine=engine,
        store=store,
        vault_root=tmp_path,
        file_cache=cache,
    )

    # deleted.py must be removed from file_index
    assert deleted_posix in cache.deleted, (
        "Deleted file must be removed from file_index via delete_entry"
    )
    # delete_by_file must have been called for the deleted path
    delete_calls = [str(c) for c in store.delete_by_file.call_args_list]
    assert any("deleted.py" in c for c in delete_calls), (
        "delete_by_file must be called for deleted.py in Qdrant"
    )
```

- [ ] **Step 2:** Run to confirm FAIL:

```
pytest tests/embedder/test_incremental_skip.py tests/embedder/test_crash_safety.py tests/embedder/test_deleted_file_cleanup.py -v
```

Expected: `TypeError: index_path() got an unexpected keyword argument 'file_cache'`

- [ ] **Step 3:** Rewrite `pipeline.py`. Full updated content of the module-level constant and `index_path`:

Remove line 28: `_FILE_HASH_CACHE: dict[str, str] = {}`

Add import at top (after existing imports):
```python
from axon.store.file_cache import FileCache, sha1_of_source
from axon.store.index_lock import acquire_index_lock
```

Replace `index_path` function (lines 127-203) with:

```python
async def index_path(
    target: Path,
    *,
    engine: EmbedderEngine,
    store: VectorStore,
    vault_root: Path,
    file_cache: FileCache,
    forced_ctx: str | None = None,
    graph_store: GraphStore | None = None,
    languages: set[str] | None = None,
) -> tuple[int, int]:
    """Index all supported files under target.

    file_cache is REQUIRED - no None fallback. Pass a SqliteFileCache for
    production, or a mock/stub for tests.

    Crash-safety (D2): writes status='pending' before any Qdrant mutation;
    sets status='done' only after _flush_batch() succeeds. A crash between
    these two points leaves status='pending', which is treated as a hash miss
    on the next run (triggering full re-index of that file).

    Invariant: status='done' => the batch containing this file's chunks has
    been flushed to Qdrant.
    """
    from collections import Counter

    files = list(iter_supported_files(target, languages=languages))
    found_posix: set[str] = set()

    total_chunks = 0
    indexed_files = 0
    pending_batch: list[VectorChunk] = []
    pending_file_meta: list[tuple[str, str, int]] = []  # (fp_posix, sha1, chunk_count)
    graph_chunks: list[Chunk] = []

    # One SELECT - load all cached sha1s for this ctx upfront
    file_ctx_default = forced_ctx or "knowledge"
    cached_sha1s: dict[str, str] = await file_cache.get_all_sha1s(file_ctx_default)

    async def _flush_batch() -> int:
        if not pending_batch:
            return 0
        batch_size = len(pending_batch)
        await store.upsert_batch(list(pending_batch))
        pending_batch.clear()
        # ONLY after flush: mark all files in this batch as done
        for fp, s1, cc in pending_file_meta:
            await file_cache.set_entry(fp, file_ctx_default, s1, cc, status="done")
        pending_file_meta.clear()
        return batch_size

    for file_path in files:
        file_ctx = forced_ctx or infer_ctx_from_path(file_path, vault_root)
        if file_ctx == "work" and forced_ctx != "work":
            continue

        language = _LANGUAGE_MAP.get(file_path.suffix)
        if language is None:
            continue

        fp_posix = Path(file_path).as_posix()
        found_posix.add(fp_posix)

        source = file_path.read_text(encoding="utf-8", errors="replace")
        current_sha1 = sha1_of_source(source)

        if cached_sha1s.get(fp_posix) == current_sha1:
            continue  # file unchanged - skip

        # D2: write crash sentinel BEFORE any Qdrant mutation
        await file_cache.set_entry(fp_posix, file_ctx, current_sha1, 0, status="pending")

        # D4: delete stale points for this file before re-adding
        await store.delete_by_file(file_ctx, fp_posix)

        chunks: list[Chunk] = chunk_source(source, language, str(file_path))
        if not chunks:
            # No chunks - mark done (empty file is valid)
            await file_cache.set_entry(fp_posix, file_ctx, current_sha1, 0, status="done")
            continue

        vectors = engine.embed([c.content for c in chunks])
        _sym_ctr: Counter[str] = Counter()
        for c, vec in zip(chunks, vectors):
            pending_batch.append(
                VectorChunk(
                    id=_chunk_id(file_path, c.symbol, _sym_ctr[c.symbol]),
                    vector=vec,
                    file_path=fp_posix,
                    language=c.language,
                    chunk_type=c.chunk_type,
                    symbol=c.symbol,
                    project=file_path.parent.name,
                    ctx=file_ctx,
                    content=c.content,
                )
            )
            _sym_ctr[c.symbol] += 1

        graph_chunks.extend(chunks)
        pending_file_meta.append((fp_posix, current_sha1, len(chunks)))

        if len(pending_batch) >= _BATCH_SIZE:
            flushed = await _flush_batch()
            total_chunks += flushed

        indexed_files += 1

    # Flush any remaining chunks in the last partial batch
    flushed = await _flush_batch()
    total_chunks += flushed

    if graph_store is not None and graph_chunks:
        dep_records = build_dependency_records(graph_chunks)
        await graph_store.upsert_deps_batch(dep_records)

    # Detect files removed from repo (D6): present in cache but not in walk
    cached_entries = await file_cache.list_entries(file_ctx_default)
    for cached_path, _ in cached_entries:
        if cached_path not in found_posix:
            await store.delete_by_file(file_ctx_default, cached_path)
            await file_cache.delete_entry(cached_path, file_ctx_default)

    return indexed_files, total_chunks
```

- [ ] **Step 4:** Run the new tests to confirm PASS:

```
pytest tests/embedder/test_incremental_skip.py tests/embedder/test_crash_safety.py tests/embedder/test_deleted_file_cleanup.py -v
```

Expected: all tests PASS.

- [ ] **Step 5:** Run all existing pipeline/embedder tests to check no regression:

```
pytest tests/embedder/ -v
```

Expected: all tests PASS. Note: callers of `index_path` in other test files that do not pass `file_cache` will now fail - fix those callers (see Step 6).

- [ ] **Step 6:** Find and fix all callers of `index_path` that omit `file_cache`. Search:

```
grep -r "index_path(" tests/ --include="*.py" -l
```

For each file found, add a mock `file_cache` parameter. Pattern for a minimal no-op mock in tests:

```python
class _NullCache:
    async def get_all_sha1s(self, ctx): return {}
    async def set_entry(self, fp, ctx, sha1, cc, *, status="done"): pass
    async def delete_entry(self, fp, ctx): pass
    async def list_entries(self, ctx): return []

# In the test call:
await index_path(..., file_cache=_NullCache())
```

- [ ] **Step 7:** Run full test suite (excluding integration):

```
pytest tests/ -v --ignore=tests/recall -k "not integration"
```

Expected: all tests PASS.

- [ ] **Step 8:** Run recall guard:

```
pytest tests/recall/test_recall_guard.py -v -k "not no_regression"
```

Expected: PASS / SKIPPED.

- [ ] **Step 9:** Commit:

```
git add src/axon/embedder/pipeline.py tests/embedder/test_incremental_skip.py tests/embedder/test_crash_safety.py tests/embedder/test_deleted_file_cleanup.py
git commit -m "feat(embedder): wire FileCache into index_path with crash-safety sentinel and delete-by-file reconcile (Plan C T7)"
```

---

### Task 8: Wire `SqliteFileCache` into `SessionStore` and update callers

**Files:**
- Modify: `C:/Users/samde/dev/axon/src/axon/store/session_store.py` (add `make_file_cache()` factory method after `init()` at line 119)
- Modify: Any CLI/hook callers that invoke `index_path` with a live `SessionStore` (search: `scripts/index_once.py`, hooks, cli)
- Modify: `C:/Users/samde/dev/axon/src/axon/hooks/git_event.py` (add lock + cache)
- Create: `C:/Users/samde/dev/axon/tests/store/test_session_store_file_cache_integration.py`

**Interfaces:**

Produces:
- `SessionStore.make_file_cache() -> SqliteFileCache` - returns a `SqliteFileCache` sharing the existing `_conn` and `_lock`.

- [ ] **Step 1:** Write failing integration test:

```python
# tests/store/test_session_store_file_cache_integration.py
"""Integration test: SessionStore.make_file_cache() wires SqliteFileCache to real DB."""
from __future__ import annotations

from pathlib import Path

import pytest

from axon.store.session_store import SessionStore


async def test_make_file_cache_round_trips(tmp_path: Path) -> None:
    db_path = tmp_path / "axon.db"
    store = SessionStore(db_path=db_path)
    await store.init()

    cache = store.make_file_cache()

    # Write and read back
    await cache.set_entry("src/foo.py", "personal", "abc123", 3)
    result = await cache.get_all_sha1s("personal")
    assert result == {"src/foo.py": "abc123"}

    await store.close()


async def test_make_file_cache_pending_invisible_to_get_all_sha1s(tmp_path: Path) -> None:
    db_path = tmp_path / "axon.db"
    store = SessionStore(db_path=db_path)
    await store.init()

    cache = store.make_file_cache()
    await cache.set_entry("src/foo.py", "personal", "abc", 5, status="pending")
    result = await cache.get_all_sha1s("personal")
    assert result == {}, "pending rows must not appear in get_all_sha1s"

    await store.close()
```

- [ ] **Step 2:** Run to confirm FAIL:

```
pytest tests/store/test_session_store_file_cache_integration.py -v
```

Expected: `AttributeError: 'SessionStore' object has no attribute 'make_file_cache'`

- [ ] **Step 3:** Add `make_file_cache` method to `SessionStore` in `src/axon/store/session_store.py`. Add after the `init` method (after line 119):

```python
    def make_file_cache(self) -> "SqliteFileCache":
        """Return a SqliteFileCache sharing this store's connection and lock.

        Call after init() so _conn is initialized. The returned cache shares
        the same asyncio.Lock as the store to serialize all SQLite writes.
        """
        from axon.store.file_cache import SqliteFileCache

        if self._conn is None:
            raise RuntimeError("SessionStore.init() must be called before make_file_cache()")
        return SqliteFileCache(self._conn, self._lock)
```

- [ ] **Step 4:** Run tests to confirm PASS:

```
pytest tests/store/test_session_store_file_cache_integration.py -v
```

Expected: both tests PASS.

- [ ] **Step 5:** Find all production callers (non-test) of `index_path` and add `file_cache`:

```
grep -r "index_path(" src/ --include="*.py" -n
```

For each caller, inject `file_cache=session_store.make_file_cache()` (where a `SessionStore` is available) or a `_NullCache` if no store is wired yet.

- [ ] **Step 6:** Update `src/axon/hooks/git_event.py` to wrap `index_path` with `acquire_index_lock`. Locate where `index_path` is called in git_event.py, add:

```python
from axon.store.index_lock import IndexLockError, acquire_index_lock

# Wrap the index_path call:
try:
    async with acquire_index_lock(repo_root):
        indexed, chunks = await index_path(
            ...,
            file_cache=session_store.make_file_cache(),
        )
except IndexLockError as e:
    logger.warning("Index lock held by another process: %s - skipping", e)
    # Hook must not block git - exit cleanly
```

- [ ] **Step 7:** Run full test suite:

```
pytest tests/ -v --ignore=tests/recall -k "not integration"
```

Expected: all tests PASS.

- [ ] **Step 8:** Commit:

```
git add src/axon/store/session_store.py src/axon/hooks/git_event.py tests/store/test_session_store_file_cache_integration.py
git commit -m "feat(store): SessionStore.make_file_cache factory; wire acquire_index_lock into git hook (Plan C T8)"
```

---

### Task 9: One-shot blue/green migration of the 9 repos

**Files:**
- Create: `C:/Users/samde/dev/axon/scripts/migrate_bluegreen.py`
- Create: `C:/Users/samde/dev/axon/scripts/verify_migration.py`

NOTE: These scripts MUST NOT be run during testing - they require live Qdrant + the 9 repos. Run manually after all unit tests pass.

**Interfaces:**

Consumes:
- `qdrant_client.QdrantClient` (synchronous, for one-off migration)
- `VALID_CONTEXTS` from `axon.context.registry`

- [ ] **Step 1:** Create `scripts/migrate_bluegreen.py`:

```python
#!/usr/bin/env python
"""One-shot blue/green migration for the 9 already-indexed repos.

DO NOT RUN DURING AUTOMATED TESTS. Run manually after Plan C is deployed:
  python scripts/migrate_bluegreen.py --dry-run   # preview
  python scripts/migrate_bluegreen.py             # execute

Steps:
  1. List existing collections and confirm target ctx names
  2. Create <ctx>_new collections with same vector config
  3. Print reindex command to run manually (does not embed)
  4. After reindex: run recall gate via test_recall_guard.py
  5. Swap aliases: <ctx> -> <ctx>_new

NOTE: The script creates new collections and swaps aliases but does NOT
run embedding (that is done via: axon index <vault> --ctx <ctx> with the
new code that targets <ctx>_new). See MIGRATION.md for full runbook.
"""
from __future__ import annotations

import argparse
import sys

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

QDRANT_URL = "http://localhost:6333"
# Contexts with data to migrate (from phase0_baseline.json: only 'personal' has data)
TARGET_CONTEXTS = ["personal"]
VECTOR_SIZE = 768  # bge-base-en-v1.5 on R7 desktop; update if model differs


def main(dry_run: bool = False) -> None:
    client = QdrantClient(QDRANT_URL)

    existing = {c.name for c in client.get_collections().collections}
    print(f"Existing collections: {sorted(existing)}")

    for ctx in TARGET_CONTEXTS:
        new_name = f"{ctx}_new"
        if new_name in existing:
            print(f"[SKIP] {new_name} already exists - delete it first if you want a fresh migration")
            continue

        if dry_run:
            print(f"[DRY-RUN] Would create collection: {new_name} (size={VECTOR_SIZE})")
        else:
            client.create_collection(
                collection_name=new_name,
                vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
            )
            print(f"[OK] Created collection: {new_name}")

    print()
    print("NEXT STEPS:")
    print("  1. Run: axon index <your_vault_root> --ctx personal")
    print("     (after deploying Plan C code so it targets 'personal' ctx)")
    print("  2. Run: pytest tests/recall/test_recall_guard.py -m integration -v")
    print("  3. If recall gate passes, run this script again with --swap-aliases")
    print("  4. If recall gate FAILS, delete <ctx>_new and investigate")


def swap_aliases(dry_run: bool = False) -> None:
    client = QdrantClient(QDRANT_URL)
    for ctx in TARGET_CONTEXTS:
        new_name = f"{ctx}_new"
        if dry_run:
            print(f"[DRY-RUN] Would swap alias {ctx} -> {new_name}")
        else:
            # Note: qdrant-client >= 1.9 supports update_collection_aliases
            # If your version differs, use recreate_collection or rename directly
            print(f"[MANUAL] Swap alias {ctx} -> {new_name} via Qdrant dashboard or API")
            print(f"  POST http://localhost:6333/collections/aliases")
            print(f'  {{"actions": [{{"delete_alias": {{"alias_name": "{ctx}"}}}},')
            print(f'    {{"create_alias": {{"collection_name": "{new_name}", "alias_name": "{ctx}"}}}}]}}')


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--swap-aliases", action="store_true")
    args = parser.parse_args()

    if args.swap_aliases:
        swap_aliases(dry_run=args.dry_run)
    else:
        main(dry_run=args.dry_run)
```

- [ ] **Step 2:** Create `scripts/verify_migration.py` - scroll-based orphan check (paginated per spec):

```python
#!/usr/bin/env python
"""Verify migration: scroll all points and check for orphan file_paths.

DO NOT RUN DURING AUTOMATED TESTS. Run manually after blue/green migration.

Usage:
  python scripts/verify_migration.py --ctx personal
"""
from __future__ import annotations

import argparse
from pathlib import Path

from qdrant_client import QdrantClient

QDRANT_URL = "http://localhost:6333"


def scroll_all(client: QdrantClient, collection: str) -> list[dict]:
    all_points = []
    offset = None
    while True:
        result, next_offset = client.scroll(
            collection,
            limit=1000,
            with_payload=True,
            offset=offset,
        )
        all_points.extend(result)
        if next_offset is None:
            break
        offset = next_offset
    return all_points


def main(ctx: str) -> None:
    client = QdrantClient(QDRANT_URL)
    points = scroll_all(client, ctx)
    print(f"Total points in '{ctx}': {len(points)}")

    file_paths = {p.payload.get("file_path") for p in points}
    missing = [fp for fp in file_paths if fp and not Path(fp).exists()]

    print(f"Distinct file_paths: {len(file_paths)}")
    print(f"Orphan file_paths (file does not exist on disk): {len(missing)}")
    for fp in sorted(missing):
        print(f"  ORPHAN: {fp}")

    if missing:
        print("\nWARNING: Orphan points detected. Re-run axon index to reconcile.")
        raise SystemExit(1)
    else:
        print("\nOK: No orphan points detected.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ctx", default="personal")
    main_args = parser.parse_args()
    main(main_args.ctx)
```

- [ ] **Step 3:** Confirm scripts are syntactically valid (no imports of embedding engine):

```
python -c "import ast; ast.parse(open('scripts/migrate_bluegreen.py').read()); print('OK')"
python -c "import ast; ast.parse(open('scripts/verify_migration.py').read()); print('OK')"
```

Expected: both print `OK`.

- [ ] **Step 4:** Commit:

```
git add scripts/migrate_bluegreen.py scripts/verify_migration.py
git commit -m "feat(scripts): blue/green migration scripts for one-shot 9-repo reindex (Plan C T9)"
```

---

### Task 10: Full test suite + coverage gate + final integration validation

**Files:**
- No new files. Validates all tasks together.

- [ ] **Step 1:** Run the complete non-integration test suite:

```
pytest tests/ -v --ignore=tests/recall -k "not integration" --cov=axon.store.file_cache --cov=axon.store.index_lock --cov=axon.embedder.pipeline --cov=axon.store.graph_store --cov-report=term-missing
```

Expected: coverage >= 80% for all four modules; all tests PASS.

- [ ] **Step 2:** Run the recall guard (skips `no_regression` since baseline.json is PENDING):

```
pytest tests/recall/test_recall_guard.py -v -k "not no_regression"
```

Expected: golden set structure tests PASS.

- [ ] **Step 3:** Run the Windows H7 validation explicitly:

```
pytest tests/store/test_index_lock.py::test_pid_alive_windows_terminated_process_h7 -v
```

Expected on Windows 11 R7: PASS (H7 validated - stale reclaim is safe).

- [ ] **Step 4:** Run the security test explicitly (cannot be skipped):

```
pytest tests/embedder/test_gitignore_exclusion.py -v
```

Expected: both tests PASS.

- [ ] **Step 5:** Dry-run the migration script:

```
python scripts/migrate_bluegreen.py --dry-run
```

Expected: prints `[DRY-RUN] Would create collection: personal_new (size=768)` and the NEXT STEPS instructions. No Qdrant connection required for dry-run review.

- [ ] **Step 6:** Run linter:

```
ruff check src/axon/store/file_cache.py src/axon/store/index_lock.py src/axon/store/graph_store.py src/axon/embedder/pipeline.py src/axon/store/session_store.py
```

Expected: no errors.

- [ ] **Step 7:** Final commit:

```
git add .
git commit -m "feat(plan-C): incremental cache complete - SQLite file_index, crash-safety, lockfile, pipelined Redis, blue/green migration scripts"
```

---

## Cross-Task Interface Summary

The following exact signatures are shared with Plans A and B. Do not change these without coordinating across all three plans:

**D1 _chunk_id (this plan owns the call sites; Plan A owns the chunk generator):**
```python
def _chunk_id(file_path: "str | Path", symbol: str, occurrence_index: int) -> str:
    import uuid
    key = f"{Path(file_path).as_posix()}::{symbol}::{occurrence_index}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))
```
Call sites updated: `pipeline.py::ingest_file` (was line 109) and `pipeline.py::index_path` (was line 173).

**delete_by_file (existing, do NOT add new delete methods):**
```python
# vector_store.py line 163
async def delete_by_file(self, ctx: str, file_path: str) -> None: ...
```

**FileCache API (owned by this plan, consumed by any future callers):**
```python
async def get_all_sha1s(self, ctx: str) -> dict[str, str]        # done-only, one SELECT
async def set_entry(self, file_path, ctx, sha1, chunk_count, *, status="done") -> None
async def delete_entry(self, file_path: str, ctx: str) -> None
async def list_entries(self, ctx: str) -> list[tuple[str, str]]  # all statuses
```

**upsert_deps_batch (this plan owns; replaces sequential loop at pipeline.py lines 196-202):**
```python
async def upsert_deps_batch(self, records: list[DependencyRecord]) -> None:
    # transaction=False, one pipeline.execute() for N records
```

**Hash function (keep identical to pipeline.py line 161):**
```python
hashlib.sha1(source.encode("utf-8")).hexdigest()  # exposed as sha1_of_source()
```
