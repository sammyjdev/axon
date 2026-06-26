# Design: Persistent incremental cache - `file_index` table in SQLite (Pillar C)

Date: 2026-06-19
Status: draft - waiting for measurement gate (Phase 0) before implementing
Scope: persist file hashes in SQLite for cross-process skip; reconcile Qdrant
points per file (delete+re-add) instead of per chunk-id; pipeline Redis upserts;
define concurrency locking; one-shot migrate the 9 already-indexed repos.

This spec covers **Pillar C** of the AXON performance overhaul (linear, cacheable,
parallel). Pillar A handles chunk cap/chunker; Pillar B handles embedding acceleration
via onnxruntime providers. This pillar is the prerequisite for the other two: without
persistent cache, no incremental execution is possible, and without per-file reconcile,
orphan points accumulate on every re-index.

---

## Context

### Root problem

The current `_FILE_HASH_CACHE` (`pipeline.py:28`) is an in-memory dict, process-scoped:

```python
# pipeline.py:28
_FILE_HASH_CACHE: dict[str, str] = {}
```

Consequence: every new invocation of the indexer (post-commit hook, `axon init`, `pb index`)
recalculates and re-embeds **all** files in the repo, even if 0 lines have changed.
For the 9 already-indexed repos, this means minutes of CPU + Qdrant I/O on every hook.

### Chunk-id instability

The `_chunk_id` is derived from `uuid5(path::symbol::start_line)` (`pipeline.py:206-211`):

```python
# pipeline.py:206-211
def _chunk_id(path: Path, chunk: Chunk) -> str:
    import uuid
    key = f"{path}::{chunk.symbol}::{chunk.start_line}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))
```

Editing 3 lines above a symbol shifts `start_line` for all chunks below -
all IDs change - the old points become orphans in Qdrant. The current upsert does not
delete the file's points before reinserting; it only does `upsert` with new IDs, leaving
the old accumulated points behind.

**Decision D1** resolves this: the new `_chunk_id` uses `uuid5(NAMESPACE_URL,
f"{file_path}::{symbol}::{occurrence_index}")` where `occurrence_index` is the 0-based
index of that symbol name within the file. This disambiguates overloads and sub-chunks
(e.g. `foo[0]`/`foo[1]`). With stable IDs, editing lines above a symbol no longer
changes that symbol's id - so no orphan points are created by line-shift.

The solution for orphans that already exist is the delete-by-file described in 3d below.

Empirical verification needed before implementing (see hypothesis ledger below):
scroll `client.scroll()` for an edited file and confirm that the count increases rather
than remaining stable.

### Non-pipelined sequential Redis

The `upsert_deps` loop in `pipeline.py:196-202`:

```python
# pipeline.py:196-202
if graph_store is not None and graph_chunks:
    for record in build_dependency_records(graph_chunks):
        await graph_store.upsert_deps(
            record.symbol,
            calls=record.calls,
            called_by=record.called_by,
        )
```

Each `upsert_deps` fires **one** Redis `hset` (`graph_store.py:34-46`). For 100 symbols
at 1 ms/round-trip latency = minimum 100 ms sequentially. Redis supports native pipelining;
it is not being used.

### Excessive memory usage hypothesis

The call to `build_dependency_records(graph_chunks)` occurs at the end of `index_path()`
(`pipeline.py:196`) after the variable `graph_chunks: list[Chunk]` (`pipeline.py:141`) has
accumulated **all** chunks from **all** files in the repo during the walk. This is the
most likely hypothesis for high memory usage in large repos. Confirmation or refutation
depends on Phase 0 profiling. Streaming `build_dependency_records` per file (process and
discard per file instead of accumulating the full list) is the proposed fix, but belongs
to the scope of **Pillar A** - not this spec. No causal claim about "14 GB" should be
treated as fact before measurement.

### Already-existing SQLite migration infrastructure

The `SessionStore` already uses `.sql` migrations in alphabetical order, tracked in
`schema_version` (`session_store.py:44-61`). Adding `003_file_index.sql` is sufficient
with no code change - `_apply_migrations()` already handles new files.

Facts verified in the code:
- `session_store.py:44-61` - `_apply_migrations()` reads migrations from `store/migrations/`,
  compares with `schema_version`, executes only the new ones. Uses `executescript()` for each
  .sql file found via `sorted(_MIGRATIONS_DIR.glob("*.sql"))`.
- `session_store.py:101` - `asyncio.Lock` present in `SessionStore.__init__`.
- `session_store.py:109-112` - WAL mode + `busy_timeout=5000` + `synchronous=NORMAL` already
  configured via PRAGMA on first connection.
- `store/migrations/000_baseline.sql` - base tables: `adr`, `session_memory`, etc.
- `store/migrations/001_axon_graph.sql` - graph tables: `nodes`, `edges`, `sessions`.
- `store/migrations/002_unique_edges.sql` - edge dedup, index `ux_edges_triple`.
- `pipeline.py:59-75` - `iter_supported_files` uses `rglob('*')` with manual dir pruning.
- `pipeline.py:161` - hash calculated as `hashlib.sha1(source.encode("utf-8")).hexdigest()`
  (UTF-8 text, NOT raw bytes).
- `vector_store.py:93-114` - `upsert_batch` groups by ctx, one Qdrant upsert per ctx.
- `vector_store.py:163-169` - `delete_by_file(ctx, file_path)` already exists; receives ctx and
  file_path, deletes by `FieldCondition(key="file_path")` filter.
- `graph_store.py:34-46` - `upsert_deps(symbol, calls, called_by)` = one Redis `hset`
  per symbol.
- `code/indexer.py:71-89` - `_iter_repo_files` already uses `git ls-files --cached --others
  --exclude-standard`; serves as reference for the version to adopt in `pipeline.py`.

---

## Hypothesis ledger (verify cheaply before implementing)

| # | Hypothesis | Cheap verification | Where to record |
|---|---|---|---|
| H1 | Orphan points already exist in the 9 indexed repos | `client.scroll()` before and after editing 1 file; check if count increases | `benchmarks/phase0_baseline.json` |
| H2 | In-memory `_FILE_HASH_CACHE` is the cause of full re-embed on every process | Log hash-hits vs misses in an `axon init` on an already-indexed repo; if 0 hits, confirmed | temporary debug log |
| H3 | Sequential Redis loop adds measurable latency (>100 ms for 100+ symbols) | `perf_counter()` around the loop in `pipeline.py:196-202` on a repo with 200+ symbols | `benchmarks/phase0_profile.json` |
| H4 | `rglob` without pruning is not the main wall-time bottleneck (embedding dominates) | `time` on `iter_supported_files()` isolated vs total wall time of `index_path()` | `benchmarks/phase0_profile.json` |
| H5 | `uuid5` collisions do not occur today in the 9 repos | Script scanning for duplicate IDs; expected result: empty dict | one-time pre-deploy check |
| H6 | `graph_chunks` accumulation is the main cause of RAM peaks in large repos | Profile RSS with `psutil` around `pipeline.py:141-196`; compare before/after streamer | `benchmarks/phase0_baseline.json` |
| H7 | `os.kill(pid, 0)` for stale lock reclaim works correctly on Windows 11 | `test_index_lock_windows.py`: create lockfile with PID of terminated process; run `acquire_index_lock`; verify reclaim occurs without error | `benchmarks/phase0_baseline.json` |

**No hypothesis can be declared a fact in the implementation plan until it has been measured.**
The Phase 0 gate (defined below) is the control.

---

## Decisions

| Topic | Decision | Rationale |
|---|---|---|
| Stable chunk-id (D1) | `uuid5(NAMESPACE_URL, f"{file_path}::{symbol}::{occurrence_index}")` where `occurrence_index` is the 0-based index of that name in the file | Eliminates forced re-indexing due to line-shift; disambiguates overloads and sub-chunks without depending on `start_line` |
| Hash cache storage | `file_index` table in the existing SQLite (same DB as `SessionStore`) | Reuses already-configured infra, migrations, locking, and WAL; zero new dependency |
| Table schema | `file_path TEXT, ctx TEXT, sha1 TEXT, status TEXT, chunk_count INTEGER, indexed_at TEXT, PRIMARY KEY (file_path, ctx)` | `status` column enables the crash-safety sentinel (D2) |
| Schema version | Migration `003_file_index.sql` in the `store/migrations/` folder | The existing `_apply_migrations()` executes without code changes |
| Crash-safety / sentinel (D2) | Write `status='pending'` + new sha BEFORE mutating Qdrant; set `status='done'` after successful upsert; on startup, treat any `pending` row as dirty and re-index | Eliminates the data loss window: crash between delete and upsert results in re-index on next run, never in stale cache + empty Qdrant |
| One-shot migration (D2 blue/green) | For the 9 already-indexed repos: index into new Qdrant collection, run recall gate, promote via alias swap only if approved | Normal incremental does not use blue/green; only the one-shot migration uses it |
| Qdrant reconcile | Delete-all-for-file + re-add, using the existing `vector_store.delete_by_file` (D4) | With D1 (stable ids), unmodified files keep valid points even with line-shift; modified files (hash miss) go through delete+upsert, eliminating deleted/renamed symbols |
| Orphans resolved by D1 + D6 | Files without hash-change keep valid points (D1 guarantees stable ids); files with hash-change do delete_by_file + re-upsert (clears removed/renamed symbols) | The orphan problem is resolved by the combination of D1 (no id change from line shift) and delete-by-file on hash-miss (clears extinct symbols); no per-chunk-id diff needed |
| Delete method | Reuse `vector_store.delete_by_file(ctx, file_path)` already in `vector_store.py:163`; call in a loop over COLLECTIONS / VALID_CONTEXTS for delete all-context when needed (D4) | Do not add any new delete method; `delete_by_file` already exists and covers the case |
| File walk (D3) | Replace `rglob` in `iter_supported_files` with `git ls-files --cached` (NOT --others) as primary source; filter each path with `git check-ignore` to exclude files that were committed and later added to .gitignore; fallback to `rglob` if not a git repo | Safety guarantee: no gitignored file is ever embedded; untracked files require `git add` before being indexed |
| File hash | Keep `hashlib.sha1(source.encode("utf-8")).hexdigest()` - identical to current code at `pipeline.py:161` | Avoids full re-embed on first deploy; any change in hash method would cause a full cold-start and must be explicitly documented as a one-time cost |
| Redis pipelining | Batch of N `hset` in a single pipeline per `upsert_deps_batch` | `redis-py` supports native `pipe()`; 3-line change, gain proportional to N symbols |
| Redis atomicity | `pipeline(transaction=False)` - no MULTI/EXEC overhead | Partial failure is corrected on the next run (reconcile will re-index the file); add a test that verifies absence of corrupted data after simulated failure |
| Mandatory `FileCache` | `FileCache` is a mandatory parameter of `index_path`; remove `if file_cache:` guards | YAGNI - the optional bypass is speculative; all callers must pass a real instance or mock |
| Concurrency locking | Lockfile `.axon/index.lock` with PID written to the file; check if PID exists via `os.kill(pid, 0)` before reclaiming stale lock; add TTL as additional fallback. HYPOTHESIS on Windows 11: `os.kill(pid, 0)` may not have the same behavior as on Unix - verify in Phase 0 with a Windows-specific test before relying on automatic reclaim in production | `O_EXCL` alone blocks future indexing after a crash; writing PID allows automatic reclaim of abandoned locks |
| Cross-platform recall score | Calibrate `min_score` separately for bge-base (768-dim) and bge-small (384-dim); store in `score_calibration.json` | A fixed threshold of 0.70 without calibration is not reliable across models with different dimensions |
| SQLite `executescript` | Treat as known behavior: `executescript` issues an implicit COMMIT before executing; `003_file_index.sql` is pure DDL with `IF NOT EXISTS` so re-executions are safe | Not a bug; documented behavior of Python's sqlite3 module |
| Paginated scroll migration | Paginate `client.scroll()` via `next_page_offset` until `None` (or use `count()` to verify zero orphans) instead of `limit=10000` | Collections with >10000 points would return silently truncated results |
| Batch sha1 cache | Add `get_all_sha1s(ctx)` to `SqliteFileCache`: a single `SELECT` returns all (file_path, sha1) for the ctx; comparison done in memory | Avoids N asyncio round-trips in the tight loop of `index_path`; reduces Lock contention |
| Path normalization | Normalize all `file_path` values stored to `Path(p).as_posix()` before writing to `file_index` and before using as Qdrant filter | Git emits `/` on all OSes; `Path` on Windows emits `\\`; inconsistency would cause lookup misses and undetected orphans |

---

## Components and changes

### 1. Migration `003_file_index.sql`

New file at `C:/Users/samde/dev/axon/src/axon/store/migrations/003_file_index.sql`:

```sql
-- 003_file_index.sql
-- Persistent per-file hash cache for cross-process incremental skip.
-- Requires: 000_baseline, 001_axon_graph, 002_unique_edges already applied.
-- executescript() emits an implicit COMMIT before running; pure DDL with
-- IF NOT EXISTS makes re-execution safe.

CREATE TABLE IF NOT EXISTS file_index (
    file_path   TEXT    NOT NULL,
    ctx         TEXT    NOT NULL,
    sha1        TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'done',  -- 'pending' | 'done'
    chunk_count INTEGER NOT NULL DEFAULT 0,
    indexed_at  TEXT    NOT NULL,  -- ISO-8601 UTC
    PRIMARY KEY (file_path, ctx)
);

CREATE INDEX IF NOT EXISTS ix_file_index_ctx
    ON file_index (ctx);

CREATE INDEX IF NOT EXISTS ix_file_index_status
    ON file_index (status);
```

Design notes:
- Composite PK `(file_path, ctx)` because the same file can be indexed in different
  contexts (e.g. `knowledge` and `work`).
- The `status` column implements the crash-safety sentinel (D2): `'pending'` indicates
  that the Qdrant mutation is in progress or was interrupted; `'done'` indicates
  consistent state.
- `chunk_count` allows validating whether the number of chunks changed without reading
  Qdrant.
- `CREATE TABLE IF NOT EXISTS` guarantees idempotency (safe re-application).
- The `_apply_migrations()` in `session_store.py:44-61` detects `003_file_index.sql` and
  executes it on the next `SessionStore` initialization - no code change required.

### 2. Module `axon/store/file_cache.py` (new)

Single responsibility: read and write `file_index`. Isolates all cache logic from
`pipeline.py`. `FileCache` is a Protocol; `SqliteFileCache` is the concrete implementation.

```python
# axon/store/file_cache.py
from __future__ import annotations
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol


class FileCache(Protocol):
    async def get_all_sha1s(self, ctx: str) -> dict[str, str]: ...
    # Returns {file_path_posix: sha1} for the given ctx (a single SELECT)

    async def set_entry(
        self, file_path: str, ctx: str, sha1: str, chunk_count: int, *,
        status: str = "done",
    ) -> None: ...

    async def delete_entry(self, file_path: str, ctx: str) -> None: ...

    async def list_entries(self, ctx: str) -> list[tuple[str, str]]: ...
    # Returns a list of (file_path_posix, sha1) for the given ctx


class SqliteFileCache:
    """Concrete implementation using the SessionStore's aiosqlite connection."""

    def __init__(self, conn, lock):  # aiosqlite.Connection, asyncio.Lock
        self._conn = conn
        self._lock = lock

    async def get_all_sha1s(self, ctx: str) -> dict[str, str]:
        """Returns {file_path: sha1} in a single SELECT; comparison done in memory."""
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
        # Normalize to posix before storing
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
                "SELECT file_path, sha1 FROM file_index WHERE ctx=?", (ctx,)
            )
            return await cur.fetchall()


def sha1_of_source(source: str) -> str:
    """Hash of the file's UTF-8 content - identical to the current pipeline.py:161.

    Do NOT use path.read_bytes() - that would produce a different digest and cause
    a full re-embed on the first deploy. If migrating to read_bytes() becomes necessary,
    document it as an explicit one-time cold-start.

    No usedforsecurity kwarg: identical to pipeline.py:161, which also does not use the kwarg.
    On FIPS systems Python may reject hashlib.sha1() without usedforsecurity=False;
    if that becomes necessary, pipeline.py:161 must be updated in the same PR to keep
    the digests identical between the two calls.
    """
    return hashlib.sha1(source.encode("utf-8")).hexdigest()
```

Dependencies: `aiosqlite` (already in use), `asyncio.Lock` (already in `SessionStore`).
No new third-party dependency.

### 3. Changes in `pipeline.py`

#### 3a. Replace `_FILE_HASH_CACHE` with `FileCache` (mandatory)

Remove `pipeline.py:28`:
```python
# REMOVER:
_FILE_HASH_CACHE: dict[str, str] = {}
```

Add `file_cache: FileCache` as a **mandatory** parameter of `index_path`.
There is no `None` fallback - all callers must pass a real instance or mock.
The previous behavior (no skip) was the result of always having an empty dict; tests
that need that behavior should pass a mock that always returns `None` for
`get_all_sha1s`.

#### 3b. New stable `_chunk_id` (D1)

Replace the current function (`pipeline.py:206-211`) with:

```python
# pipeline.py - replaces the _chunk_id function
def _chunk_id(file_path: Path, chunk: Chunk, occurrence_index: int) -> str:
    """Stable ID: does not depend on start_line.

    occurrence_index = 0-based index of that symbol name within the file
    (e.g. the second method named 'process' has occurrence_index=1).
    Disambiguates overloads and sub-chunks (foo[0]/foo[1]) without using a line number.
    """
    import uuid
    key = f"{Path(file_path).as_posix()}::{chunk.symbol}::{occurrence_index}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))
```

The caller must track a `Counter[str]` of symbol names per file and pass
`counter[chunk.symbol]` as `occurrence_index` before incrementing.

#### 3c. Incremental skip logic with crash-safety sentinel (D2)

```python
# Pseudocode - index_path() after receiving file_cache: FileCache

# --- Pre-load all sha1s in a single SELECT ---
cached_sha1s: dict[str, str] = await file_cache.get_all_sha1s(ctx)
# cached_sha1s contains only rows with status='done'

# Any 'pending' row that survived a crash will be invisible here ->
# absent from cached_sha1s -> treated as a hash miss -> re-indexed

# --- Per-file loop ---
for file_path in files:
    fp_posix = Path(file_path).as_posix()
    source = file_path.read_text(encoding="utf-8", errors="replace")
    current_sha1 = sha1_of_source(source)

    if cached_sha1s.get(fp_posix) == current_sha1:
        stats["skipped"] += 1
        continue  # file unchanged - skip

    # (1) Write sentinel BEFORE mutating Qdrant
    await file_cache.set_entry(fp_posix, ctx, current_sha1, 0, status="pending")

    # (2) Delete old points; accumulate chunks in the deferred batch
    await store.delete_by_file(ctx, fp_posix)
    chunks = chunk_source(source, language, str(file_path))
    pending_batch.extend(chunks)
    pending_file_meta.append((fp_posix, current_sha1, len(chunks)))
    # IMPORTANT: set_entry(done) does NOT happen here inside the loop.
    # Chunks have not yet been upserted to Qdrant (they are in pending_batch).
    # Marking done before the flush would cause: crash => cache='done' but Qdrant empty.

    if len(pending_batch) >= _BATCH_SIZE:
        # (3a) Flush the batch BEFORE marking done
        await _flush_batch(pending_batch, engine, store, ctx)
        pending_batch.clear()
        # (3b) Only after a successful flush, mark all files in the batch as done
        for fp, s1, cc in pending_file_meta:
            await file_cache.set_entry(fp, ctx, s1, cc, status="done")
        pending_file_meta.clear()

# After the loop: flush the last partial batch, then mark done
await _flush_batch(pending_batch, engine, store, ctx)
for fp, s1, cc in pending_file_meta:
    await file_cache.set_entry(fp, ctx, s1, cc, status="done")
```

If the process crashes between (1) and (3b), the row remains `status='pending'`. On the
next run, `get_all_sha1s` filters only `status='done'`, so the file is treated as a
miss and re-indexed completely. The invariant is: `status='done'` implies that the batch
containing that file's chunks has already been persisted to Qdrant.

#### 3d. Replace `rglob` with `git ls-files` in `iter_supported_files` (D3)

Change `pipeline.py:59-75` to use `git ls-files --cached` (NOT --others) as the
primary source, with `git check-ignore` filtering for files that were committed and
later added to .gitignore:

```python
# Pseudocode - iter_supported_files (D3)
import subprocess

def iter_supported_files(target: Path, *, languages: set[str] | None = None):
    if target.is_file():
        language = _language_for_suffix(target.suffix)
        if language and (languages is None or language in languages):
            yield target
        return

    try:
        result = subprocess.run(
            ["git", "-C", str(target), "ls-files", "--cached"],
            capture_output=True, text=True, check=True,
        )
        for line in result.stdout.splitlines():
            p = target / line.strip()
            # Normalize for consistent comparisons
            if p.suffix not in _LANGUAGE_MAP:
                continue
            if not p.is_file():
                continue
            # Exclude files that were committed and later gitignored
            chk = subprocess.run(
                ["git", "-C", str(target), "check-ignore", "-q", str(p)],
                capture_output=True,
            )
            if chk.returncode == 0:
                continue  # gitignored - do not embed
            language = _language_for_suffix(p.suffix)
            if language and (languages is None or language in languages):
                yield p
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Fallback: rglob with manual pruning (non-git repo or git not available)
        for p in target.rglob("*"):
            if any(part in EXCLUDED_DIR_NAMES for part in p.parts):
                continue
            language = _language_for_suffix(p.suffix)
            if p.is_file() and language and (languages is None or language in languages):
                yield p
```

Safety guarantee (D3): `git ls-files --cached` lists only tracked files.
Untracked files (not submitted to `git add`) do not appear. The `git check-ignore`
filter excludes files that were committed and then added to .gitignore.
Result: **no gitignored file is ever embedded**. Add a mandatory safety test (see tests section).

All `file_path` values stored must be normalized via `Path(p).as_posix()` before
any write to `file_index` or use as Qdrant filter - avoids mismatch between paths
emitted by git (always `/`) and `Path` on Windows (emits `\\`).

#### 3e. Qdrant reconcile per file (delete-then-upsert) (D4 + D6)

Before chunk/embed of a modified file (hash miss), delete all points for the
file in that ctx using the **already existing** `vector_store.delete_by_file` method:

```python
# Pseudocode - after detecting a hash miss, before chunk/embed
# Do NOT create a new method; use delete_by_file that already exists in vector_store.py:163
await store.delete_by_file(ctx, fp_posix)
```

For delete all-context (e.g. file deleted from repo), call in a loop:

```python
# Pseudocode - file removed from the repo
from axon.context.registry import VALID_CONTEXTS
for ctx_name in VALID_CONTEXTS:
    await store.delete_by_file(ctx_name, fp_posix)
```

Do not add `delete_file_points`, `delete_by_file_path`, `_collections()`, or any
other delete method. The existing `delete_by_file` is sufficient (D4).

**How D1 + D6 resolve the orphan problem:**
- **Unmodified file** (hash hit): ids are stable (D1 guarantees that line-shift does not
  change the id), so existing points remain valid. No re-indexing.
- **Modified file** (hash miss): `delete_by_file` removes all points for the file
  before the upsert. Deleted or renamed symbols are not re-inserted - orphans
  eliminated automatically.

#### 3f. Deleted file detection

After the file walk, compare the list of found files with `list_entries(ctx)`
from the cache, **scoped to the same ctx**. Files in the cache for that ctx but absent in
the walk have been deleted:

```python
# Pseudocode - at the end of index_path(), before returning
# found_paths contains only paths of the current ctx (do not mix ctxs)
found_paths = {Path(p).as_posix() for p in iterated_files_for_this_ctx}
cached_entries = await file_cache.list_entries(ctx)  # scoped to the same ctx
for cached_path, _ in cached_entries:
    if cached_path not in found_paths:
        await store.delete_by_file(ctx, cached_path)
        await file_cache.delete_entry(cached_path, ctx)
        stats["deleted"] += 1
```

The comparison uses only entries from the same ctx, avoiding false positives where a file
exists in `knowledge` but not in `work`.

### 4. Redis pipelining in `pipeline.py` and `graph_store.py`

#### 4a. Current `upsert_deps` signature (`graph_store.py:34-46`)

```python
# graph_store.py:34-46 - ATUAL
async def upsert_deps(
    self,
    symbol: str,
    calls: list[str],
    called_by: list[str],
) -> None:
    await self._redis.hset(
        f"dep:{symbol}",
        mapping={
            "calls": json.dumps(calls),
            "called_by": json.dumps(called_by),
        },
    )
```

#### 4b. New `upsert_deps_batch` method in `graph_store.py`

```python
# graph_store.py - novo metodo
async def upsert_deps_batch(
    self,
    records: list[DependencyRecord],  # DependencyRecord tem .symbol, .calls, .called_by
) -> None:
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

`transaction=False` avoids the `MULTI/EXEC` overhead for upserts that do not require
atomicity across distinct symbols. Partial failure is corrected on the next run of
`index_path` (the file will be re-indexed by hash miss or new edit). Add a
test that simulates mid-pipeline failure and verifies absence of corrupted data.

#### 4c. Call in `pipeline.py`

```python
# pipeline.py - substituir loop sequencial por batch
await graph_store.upsert_deps_batch(dep_records)
```

Gain verification: measure `perf_counter()` around the current loop on a repo with 200+
symbols before deploying (hypothesis H3 in the ledger). If the gain is < 20 ms, the pipeline
is still worth it for round-trip reduction but is not urgent.

### 5. Locking and concurrency

#### Risk scenario

The git hook (`python -m axon.hooks.git_event post-commit`) and a manual `axon index` can
be triggered in parallel - separate processes, same repo.

#### Protection layers

| Layer | Mechanism | Covers |
|---|---|---|
| SQLite WAL | `journal_mode=WAL` + `busy_timeout=5000` (`session_store.py:109-112`) | Two processes reading/writing `file_index` simultaneously |
| `asyncio.Lock` | Existing lock in `SessionStore.__init__` (line 101), passed to `SqliteFileCache` | Concurrent coroutines in the same process |
| Qdrant | Qdrant accepts concurrent upserts and deletes without data corruption | Duplicate-upsert possible; resolved by per-file reconcile |
| `.axon/index.lock` file | Lockfile with PID; `os.kill(pid, 0)` check for stale lock reclaim | Prevents two processes from indexing the same repo simultaneously; lock abandoned by crash is reclaimed automatically |

The lockfile with PID resolves the stale lock problem:

```python
# axon/store/index_lock.py - novo modulo
import os
from contextlib import asynccontextmanager
from pathlib import Path


class IndexLockError(Exception):
    pass


def _pid_alive(pid: int) -> bool:
    """Returns True if the process with the given pid is still running.

    HYPOTHESIS (verify in Phase 0 on Windows 11):
    - On Unix/macOS: os.kill(pid, 0) raises ProcessLookupError if the PID does not exist,
      PermissionError if it exists but belongs to another user - reliable behavior.
    - On Windows 11 (R7 5800X3D): os.kill() is implemented via TerminateProcess() with
      signal=0 having no standardized effect; the behavior may differ.
      Add a Windows-specific integration test (test_index_lock_windows.py)
      that creates a lockfile with the PID of an already-terminated process and verifies that the
      automatic reclaim works correctly before relying on this logic in production.
    """
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


@asynccontextmanager
async def acquire_index_lock(repo_root: Path):
    lock_path = repo_root / ".axon" / "index.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    if lock_path.exists():
        try:
            existing_pid = int(lock_path.read_text().strip())
            if _pid_alive(existing_pid):
                raise IndexLockError(
                    f"Another process (pid={existing_pid}) is indexing {repo_root}. "
                    f"If the previous process crashed, remove: {lock_path}"
                )
            # PID no longer exists - lock abandoned by a crash, reclaim it
            lock_path.unlink(missing_ok=True)
        except ValueError:
            # Lock file with invalid content - reclaim it
            lock_path.unlink(missing_ok=True)

    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except FileExistsError:
        raise IndexLockError(
            f"Race condition ao adquirir lock em {lock_path}. Tentar novamente."
        )
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)
```

The post-commit hook must never block git. If the lockfile exists and the PID is
active, the hook logs a warning and exits with exit 0 (identical behavior to the pattern in
`git_installer.py` where failures are swallowed via `|| true`). Indexing will be done
on the next commit or via manual `axon index`.

Add a test that verifies automatic reclaim of stale lock (non-existent pid).
Add `test_index_lock_windows.py` specifically for the R7 5800X3D (Windows 11):
verify that `_pid_alive` returns False for a terminated process's PID and that the
reclaim proceeds correctly. This test is mandatory before declaring reclaim as a
supported feature on Windows - until validated, treat as hypothesis H7
(see hypothesis ledger).

### 6. One-shot migration of the 9 already-indexed repos (D2 blue/green)

#### Context

The 9 repos were indexed with the old logic (without `file_index`, possibly with ctx
`personal` or other legacy contexts). After deploying this pillar, the SQLite cache will
be empty for all of them.

The problem is that orphan points already exist in Qdrant (hypothesis H1). Re-indexing
without purge only adds new points on top of old ones.

#### Blue/green procedure (for one-shot migration only)

The one-shot migration uses blue/green to guarantee rollback without downtime. Normal
incremental runs **do not** use blue/green.

```bash
# Step 1 - list existing collections and confirm legacy ctx names
python - <<'EOF'
from qdrant_client import QdrantClient
client = QdrantClient("http://localhost:6333")
for col in client.get_collections().collections:
    print(col.name)
EOF

# Step 2 - create new collections with the _new suffix (blue/green)
# (replace "knowledge" with the real ctx(es) confirmed in step 1)
python - <<'EOF'
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance
client = QdrantClient("http://localhost:6333")
# Example for ctx "knowledge" - repeat for each ctx to migrate
client.create_collection(
    collection_name="knowledge_new",
    vectors_config=VectorParams(size=768, distance=Distance.COSINE),
)
EOF

# Step 3 - full re-index of the 9 repos pointing at the _new collections
# (requires a target_collection parameter in the indexer or a temporary rename)
axon index <vault_root> --ctx knowledge --target-collection knowledge_new

# Step 4 - run the recall gate against knowledge_new
# (fill in the 20 golden-set queries against the new collection)
# Gate: Top-1 >= 0.90, Top-3 >= 0.95, score >= 0.90
# If it fails, keep the old collection and investigate before proceeding

# Step 5 - promote via alias swap ONLY if the recall gate passed
python - <<'EOF'
from qdrant_client import QdrantClient
client = QdrantClient("http://localhost:6333")
# Atomic alias swap
client.update_collection_aliases(change_aliases_operations=[
    {"delete_alias": {"alias_name": "knowledge"}},
    {"create_alias": {"collection_name": "knowledge_new", "alias_name": "knowledge"}},
])
EOF

# Step 6 - verify absence of orphans post-migration via paginated scroll
python - <<'EOF'
from qdrant_client import QdrantClient
client = QdrantClient("http://localhost:6333")
all_points = []
offset = None
while True:
    result, next_offset = client.scroll(
        "knowledge", limit=1000, with_payload=True, offset=offset
    )
    all_points.extend(result)
    if next_offset is None:
        break
    offset = next_offset
paths = {p.payload.get("file_path") for p in all_points}
print(f"Total points: {len(all_points)}")
print(f"Distinct paths: {len(paths)}")
# Manually inspect whether any path is unexpected
EOF
```

The full re-index is needed only once. After that, `file_index` has correct state
and all subsequent refreshes will be incremental.

### 7. Cross-platform score calibration

The `min_score` threshold in the golden set cannot be a fixed value without calibration
across models with different dimensions (bge-base 768-dim on the R7 desktop vs bge-small
384-dim on M1 Pro). Calibration must be done once on each machine and stored in
`tests/recall/score_calibration.json`:

```json
{
  "bge-base-en-v1.5": { "min_score": 0.XX, "calibrated_at": "2026-..." },
  "bge-small-en-v1.5": { "min_score": 0.XX, "calibrated_at": "2026-..." }
}
```

The recall harness reads `score_calibration.json` and uses the correct threshold for
the active model. The XX values must be determined experimentally in Phase 0, not assumed.

---

## Data flow (after this pillar)

```
axon index <repo> --ctx knowledge
    |
    +-- acquire_index_lock(repo_root)   # prevents multi-process concurrency
    |                                   # automatic reclaim of stale lock (PID)
    |
    +-- cached_sha1s = await file_cache.get_all_sha1s(ctx)  # one SELECT
    |   # rows with status='pending' are filtered out -> treated as hash miss
    |
    +-- iter_supported_files(repo)      # git ls-files --cached + git check-ignore
    |   pending_file_meta = []  # accumulates (fp_posix, sha1, chunk_count) until after flush
    |   for each file (normalize path to posix):
    |     current_sha1 = sha1_of_source(source)
    |     if cached_sha1s.get(fp_posix) == current_sha1:  SKIP
    |     else:
    |       # (1) sentinel BEFORE mutating Qdrant
    |       await file_cache.set_entry(fp_posix, ctx, sha1, 0, status="pending")
    |       # (2) delete; chunks go into the deferred batch (NOT upserted yet)
    |       await store.delete_by_file(ctx, fp_posix)  # existing method
    |       chunks = chunk_source(source, language, str(file_path))
    |       pending_batch.extend(chunks)
    |       pending_file_meta.append((fp_posix, sha1, len(chunks)))
    |       if len(pending_batch) >= _BATCH_SIZE:
    |         # (3) flush BEFORE marking done - guarantees chunks persisted in Qdrant
    |         await _flush_batch(pending_batch, engine, store, ctx)
    |         pending_batch.clear()
    |         for fp, s1, cc in pending_file_meta:
    |           await file_cache.set_entry(fp, ctx, s1, cc, status="done")
    |         pending_file_meta.clear()
    |         # Invariant: status='done' => chunks already persisted in Qdrant
    |
    +-- _flush_batch (last remaining batch)
    +-- for each (fp, s1, cc) in pending_file_meta:
    |     await file_cache.set_entry(fp, ctx, s1, cc, status="done")
    |   # set_entry(done) only happens AFTER the flush that contains the file's chunks
    |
    +-- build_dependency_records(graph_chunks)   # 2nd parse - streaming per file
    |   is Pillar A scope; here it is still accumulated to keep compatibility
    +-- await graph_store.upsert_deps_batch(dep_records)  # pipelined
    |
    +-- deleted-file detection (list_entries vs found_paths, scoped to the ctx)
    |   for each deleted: delete_by_file(ctx, path) + delete_entry(path, ctx)
    |
    +-- release_index_lock()
```

---

## Phase 0 gate (implementation prerequisite)

**No line of code from this pillar can be merged until all conditions below are satisfied
and recorded in `benchmarks/phase0_baseline.json`.**

| Condition | Target metric | How to measure |
|---|---|---|
| Throughput baseline captured | Record chunks/s on the synthetic corpus of 500 functions | `time index_path()` on fixed corpus |
| Peak RSS baseline captured | Record MB on the 9 repos | `psutil` sampled every 2 s |
| H1 verified | Confirm whether orphans exist today | Paginated `scroll()` before/after editing 1 file |
| H3 verified | Measure Redis loop latency on 200+ symbols | `perf_counter()` around `pipeline.py:196-202` |
| H4 verified | Measure wall time of `rglob` isolated vs total | `time iter_supported_files()` isolated |
| H6 verified | Measure RSS before/after `build_dependency_records` vs accumulator | `psutil.Process().memory_info().rss` at breakpoint |
| GPU available (Pillar B) | `bool` in `phase0_baseline.json` | `ort.get_available_providers()` |
| Recall baseline >= 0.80 | Top-1 and Top-3 on the 20-query golden set | Recall harness (see below) |
| Score calibrated per model | `score_calibration.json` filled for both models | Experimental measurement on R7 and M1 Pro |

If peak RSS exceeds 8 GB during baseline measurement, the evidence must be recorded
and communicated to Pillar A (which owns `build_dependency_records` streaming). This
pillar does not implement streaming - it only reports Phase 0 data.

---

## Recall/quality guard

This pillar does not touch the chunker or the embedder directly, but the per-file
reconcile (delete-then-upsert) could, in theory, alter which points are in Qdrant. The
recall guard is mandatory before and after deploy.

### Golden set (fixed, 20 queries)

File: `tests/recall/golden_set.json` (created manually, never auto-generated).

Distribution:
- 8 Python queries (function, method, short utility)
- 5 Java queries (class, interface method, enum)
- 4 TypeScript queries (function, arrow function, exported type)
- 3 cross-file/architectural queries

Each entry:
```json
{
  "query": "semantic search string",
  "expected_file": "normalized/posix/path.py",
  "expected_symbol": "function_or_class_name",
  "min_score": "<see score_calibration.json for the active model>"
}
```

### Gate metrics

| Metric | Target |
|---|---|
| Top-1 hit rate (hits[0].file_path == expected) | >= 0.90 |
| Top-3 hit rate (expected in hits[0..2]) | >= 0.95 |
| Overall score (BenchmarkRunSummary.score) | >= 0.90 |

Any regression vs `tests/recall/baseline.json` blocks the merge.

### Cross-platform stability

The same queries and expected_files must pass on R7 5800X3D and M1 Pro. The `min_score`
threshold is read from `score_calibration.json` for the active model on each machine
(bge-base 768-dim on desktop, bge-small 384-dim on mac). If a query/expected_file pair
fails on mac, the golden set must be revised before deploying.

---

## Measurable success criteria (per machine)

| Metric | R7 5800X3D | M1 Pro | How to measure |
|---|---|---|---|
| Full index wall time (9 repos, cold cache) | <= 5 min | <= 8 min | `time axon index <vault>` with empty cache; median of 3 runs |
| Incremental refresh wall time (1 file, 10-50 chunks) | <= 10 s | <= 15 s | 5 files of varying sizes (10/20/30/40/50 chunks); all must pass |
| Post-commit hook wall time (20 changed files) | <= 30 s | <= 45 s | `python -m axon.hooks.git_event post-commit` timed; maximum of 3 runs |
| Peak RSS full index (9 repos) | <= 2 GB | <= 1.5 GB | `psutil.Process().memory_info().rss` sampled every 2 s |
| Embedding throughput (chunks/s end-to-end) | >= 300 chunks/s | >= 200 chunks/s | fixed synthetic corpus of 500 Python functions (15-30 lines each) |
| Recall Top-1 (golden set 20 queries) | >= 0.90 | >= 0.90 | harness on real Qdrant with reference corpus (`src/axon/embedder/`, `src/axon/store/`) |
| Recall Top-3 (golden set 20 queries) | >= 0.95 | >= 0.95 | same harness |
| Gitignored file exclusion | 0 points whose file_path matches .gitignore | 0 points | Qdrant scroll post-index on repo with `.env` and `secrets.json` gitignored |
| Orphan-free post-reconcile | 0 orphan points after editing 3 lines above a symbol | 0 orphan points | scroll by file_path before and after; count must be equal (no accumulation) |
| Concurrency safety | 0 corruptions in 20 trials of simultaneous index+hook | 0 corruptions | 2 parallel processes via subprocess; scroll post-execution; no duplicate IDs or invalid JSON in Redis |
| Stale lock reclaim | Lock abandoned by crash reclaimed automatically | same | integration test: create lock with fake PID, run index_path, confirm success |

---

## Units (isolation and testability)

| Module | Responsibility | Injectable dependencies |
|---|---|---|
| `axon/store/file_cache.py::SqliteFileCache` | CRUD on `file_index`; sha1 calculation | `aiosqlite.Connection`, `asyncio.Lock` |
| `axon/store/index_lock.py::acquire_index_lock` | Lockfile with PID; stale reclaim | `Path` (repo root) |
| `axon/store/graph_store.py::upsert_deps_batch` | Redis batch pipeline | `redis.asyncio.Redis` |
| `axon/embedder/vector_store.py::delete_by_file` | Qdrant delete by (ctx, file_path) - ALREADY EXISTS | `AsyncQdrantClient` |
| `axon/embedder/pipeline.py::index_path` (modified) | Orchestrates skip, reconcile, flush, orphan-delete | `FileCache`, `VectorStore`, `GraphStore`, `EmbedderEngine` |
| `axon/store/migrations/003_file_index.sql` | Table schema with status column | n/a - pure SQL |

Each unit is testable with injected mocks:
- `SqliteFileCache`: test `get_all_sha1s` miss/hit, `set_entry` UPSERT with status,
  `delete_entry`, `list_entries` filtering by ctx.
- `acquire_index_lock`: test lock acquired, lock of active PID (raises `IndexLockError`),
  lock of non-existent PID (automatic reclaim), release in `finally`.
- `upsert_deps_batch`: mock of Redis `pipeline()`; verify that N symbols result in
  exactly 1 `pipe.execute()`.
- `delete_by_file` (existing): mock of `AsyncQdrantClient.delete`; verify `file_path`
  filter.
- `index_path` with mocked `FileCache`: verify that files with identical sha1 are
  skipped; modified files go through the sentinel-pending / delete_by_file /
  upsert / sentinel-done cycle.

---

## End-to-end verification

1. **Incremental skip:** index a repo; without modifying any file, re-run `axon index`;
   verify that output shows `0 files re-embedded` (all skipped by cache with
   `status='done'`).

2. **Orphan reconcile (D1 + D6):** index a Python file with 5 functions; edit 3
   lines before the first function (before: ids based on start_line changed; now with
   D1 ids are stable); re-index; Qdrant scroll for that `file_path`; count should
   be 5 (not 10) regardless of line-shift.

3. **Deleted file:** index repo; delete 1 file; re-index; Qdrant scroll for the
   deleted `file_path` should return 0 points. `file_index` should not contain the entry.

4. **Gitignore guard (D3 - mandatory safety test):** create `.env` in the repo with
   `SECRET=abc`; `git add .env`; add `.env` to `.gitignore`; `axon index`;
   Qdrant scroll by `file_path` containing `.env`; must return 0 results. The file
   cannot appear in Qdrant under any circumstance.

5. **Concurrency:** launch `axon index <repo>` and `python -m axon.hooks.git_event post-commit`
   via `subprocess` simultaneously; after both finish, Qdrant scroll and verify
   absence of duplicate IDs; check Redis for invalid JSON in `dep:*` keys. Repeat
   20 times.

6. **Lockfile with PID:** during an ongoing `axon index` (artificially slowed
   via sleep in integration test), attempt a second `axon index` on the same repo; the
   second should exit with `another process indexing` warning and exit 0 (no stacktrace).
   After the first finishes, create lock with invalid PID (e.g. 99999999) and verify that
   the next `axon index` reclaims the lock and proceeds normally.

7. **Crash-safety (D2):** simulate crash between the `status='pending'` sentinel and
   `status='done'` (e.g. `KeyboardInterrupt` mid-upsert); verify that on the next
   run the file is fully re-indexed and `status` ends up `done`.

8. **Path normalization on Windows:** create a file whose path would be emitted with `\\`
   by Windows `Path`; verify that the lookup in `file_index` and Qdrant uses the posix
   form and finds the correct record.

9. **One-shot migration:** after purging legacy collections and full re-index via
   blue/green, run `axon search_code "known function"`; must return hits from the 9
   repos in ctx `knowledge`.

---

## Tests

### Unit

- `test_file_cache.py`:
  - `test_get_all_sha1s_empty`: no entry in cache -> empty dict.
  - `test_get_all_sha1s_filters_done`: entries with `status='pending'` do not appear.
  - `test_get_all_sha1s_hit`: file in cache with `status='done'` -> correct sha1.
  - `test_set_entry_upsert`: second call with different sha1 updates the row.
  - `test_set_entry_pending_then_done`: set pending, then done, only done appears
    in `get_all_sha1s`.
  - `test_delete_entry`: entry removed, absent from `get_all_sha1s`.
  - `test_list_entries_filters_by_ctx`: entries from ctx `work` do not appear in `knowledge`.
  - `test_path_normalization`: path with backslash stored and read as posix.

- `test_index_lock.py`:
  - `test_acquire_releases_on_exit`: lockfile removed after block.
  - `test_acquire_raises_if_pid_alive`: second `acquire` with active PID raises
    `IndexLockError`.
  - `test_acquire_reclaims_stale_lock`: lock with non-existent PID is reclaimed and
    indexing proceeds normally.
  - `test_acquire_releases_on_exception`: lockfile removed even on internal exception.
  - `test_index_lock_windows.py` (Windows 11 / R7 5800X3D - hypothesis H7): verify that
    `_pid_alive` returns False for a terminated process's PID and that reclaim occurs
    correctly. Mark with `@pytest.mark.skipif(sys.platform != 'win32', ...)`.
    MANDATORY before declaring reclaim as supported on Windows.

- `test_upsert_deps_batch.py`:
  - `test_batch_single_pipeline_call`: N symbols result in exactly 1 `pipe.execute()`.
  - `test_empty_batch_no_op`: empty list does not call `pipeline()`.
  - `test_partial_failure_no_corrupt_data`: simulated failure in `pipe.execute()` does not
    leave malformed data in existing `dep:*` keys.

- `test_chunk_id_stable.py`:
  - `test_id_stable_after_line_shift`: same symbol with different `start_line` produces
    identical id (D1 - occurrence_index is used, not start_line).
  - `test_id_disambiguates_overloads`: two methods with the same name in the same file
    receive distinct ids (occurrence_index 0 vs 1).

### Integration

- `test_incremental_skip.py`: index + re-index without changes; mock embedder; assert
  that `engine.embed()` was not called on the second run.
- `test_orphan_reconcile.py`: index -> edit file -> re-index -> Qdrant scroll;
  count identical before/after (no accumulation).
- `test_deleted_file_cleanup.py`: index -> delete file -> re-index -> scroll = 0.
- `test_gitignore_exclusion.py`: file committed and then added to .gitignore ->
  index -> scroll = 0. (SAFETY TEST - mandatory, cannot be skipped for coverage.)
- `test_crash_safety.py`: `pending` sentinel survives simulated crash; next run
  re-indexes and sets `done`.
- `test_cross_ctx_no_false_positive.py`: file existing in `knowledge` but absent
  in `work` is not deleted from `knowledge` Qdrant during `work` index.

### Recall regression

- `test_recall_guard.py`: loads `tests/recall/baseline.json`; runs 20-query harness
  against real Qdrant (testcontainers); `compare_benchmark_runs(current, baseline)`;
  `assert len(report.regressions) == 0` and `assert summary.score >= 0.90`.
  The `min_score` threshold per query is read from `score_calibration.json`.

### Coverage

Minimum 80% on new/modified modules: `file_cache.py`, `index_lock.py`, the
`graph_store.py` module on added functions, `pipeline.py` on skip/reconcile/
delete/sentinel paths.

---

## Out of scope

- **Elimination of double-parse in `graph_extractor.py`:** `extract_calls()` re-parses the
  chunk content; unifying with the chunker's parse reduces CPU but is a larger architectural
  change. Address in Pillar A or a dedicated spec.
- **Chunk size cap (Python/TypeScript):** `_MAX_CHUNK_LINES` only exists for
  Java (`chunker.py:37`). Adding a cap for Python/TypeScript is Pillar A scope.
- **GPU / `CUDAExecutionProvider`:** Pillar B scope (onnxruntime providers). This pillar
  does not touch `EmbedderEngine`.
- **Custom multiprocessing (worker pool):** YAGNI until Pillar B measures throughput
  with onnxruntime's native threading.
- **Streaming `build_dependency_records`:** the hypothesis that `graph_chunks` accumulation
  causes RAM peaks is recorded as H6 and will be confirmed or refuted in Phase 0. The fix
  (stream per file) belongs to Pillar A.
- **Support for new languages (Rust, Go, Bash):** chunker scope, not this pillar.
- **SQLite migration rollback:** the current migration system has no down-migration;
  adding that mechanism is a separate DB infra scope.
