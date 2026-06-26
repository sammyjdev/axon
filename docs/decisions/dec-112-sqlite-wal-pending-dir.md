# dec-112 - SQLite WAL + pending dir + idempotent drain; no daemon

- Status: accepted
- Date: 2026-05-27

## Context

`SessionStore` (`src/axon/store/session_store.py`) currently uses `aiosqlite`
with an in-process `asyncio.Lock()` and **no `PRAGMA journal_mode=WAL`**
(default rollback journal). Each CLI invocation opens its own connection.
Concurrent multi-process access (two parallel hooks, an agent and dev CLI
simultaneously) causes real lock contention.

Red-team R1 identified `database is locked` risk under multi-agent load.
R2 proposed a daemon + Unix socket; **rejected** because it breaks native
Windows and introduces complex IPC. R3 identified that retry without a
defined fallback causes state drift. R4 identified a write/drain race in
the fallback file. R5 identified lack of error isolation in the drain.

## Decision

### SQLite concurrency

`SessionStore._connection()` applies on connect:

```sql
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;
PRAGMA synchronous=NORMAL;
```

Cross-process multi-writer via native SQLite. WAL allows concurrent readers
during writes.

### Retry with fallback

Under `SQLITE_BUSY`, writes follow:

```
retry with exponential backoff + jitter
total budget: 2 * busy_timeout = 10s
after exhausting:
  1. write the capture to .axon/pending/{commit_hash}-{ts_ns}.json
     (unique path by construction; atomic rename)
  2. emit structured warning to .axon/capture-warnings.jsonl
  3. return success to the hook (NEVER breaks git)
```

Hard guarantee: **the hook never breaks git because of a capture.**

### Pending dir, not a single fallback file

`.axon/context.md` stops being an active sink. It becomes a **derived view**,
regenerated from consumed `pending/` + SessionStore state.

Advantages of pending dir over a single file:

- Unique paths (`commit_hash + ts_ns`) eliminate collisions by construction
- `rename` on a POSIX filesystem is atomic
- No need for `flock` or append-only with PIPE_BUF
- Drainer enumerates, processes in chronological order via stat, deletes
- Crash mid-drain: file remains in `pending/`, next drain processes it
- Natural idempotence: key `(commit_hash, ts_ns)` is unique; reprocessing
  is safe

### Drain

Triggered by:

- Next successful `pb capture-*`
- `post-merge` / `post-checkout` hook
- `pb doctor` (informational)
- Manual `pb drain`

Drain loop:

```
for each file in .axon/pending/ (chronological order):
  try:
    parse JSON
    write to SessionStore (with SQLite retry)
    delete file
  except (JSONDecodeError, UnicodeError, ValueError, ...):
    move to .axon/pending-quarantine/{basename}.{ts}.json
    append to .axon/quarantine.jsonl: {original_path, reason,
                                       exception, ts}
    continue  # does not block the loop
  except SQLITE_BUSY after retry exhausted:
    leave in pending/, next drain tries again
```

Quarantine is **never** deleted automatically - preserves evidence
for debugging. `pb pending recover [--id=X]` allows manual retry.

### Do not implement

- Daemon process
- Unix socket / Named Pipes / HTTP loopback
- External queue
- Optional Postgres
- `flock` or other coordination primitives

## Rationale

- **Real load is low**: local dev hooks generate ~1 write/s
  sustained; SQLite WAL handles ~100 writes/s without contention.
- **Pending dir + unique paths** eliminates write races by
  construction, without depending on POSIX primitives.
- **Daemon was overengineering**: broke native Windows, introduced
  complex IPC, and the load does not justify it.
- **Fallback file (dec-103)** remains valid as a derived view
  for agents without MCP, now fed by the consumed drain.
- **Quarantine pattern** standard resilient-queue pattern: corrupted
  payload does not block processing of valid ones.

## Consequences

- `SessionStore._connection()` applies PRAGMAs on open.
- Writes wrapped in a retry helper (`axon.store.retry`).
- New module `axon.store.pending` with `write()`, `drain()`,
  `quarantine_invalid()`.
- `.axon/pending/`, `.axon/pending-quarantine/`,
  `.axon/capture-warnings.jsonl`, `.axon/quarantine.jsonl` added
  to the repo layout.
- `.gitignore` should include `.axon/pending/`,
  `.axon/pending-quarantine/`, `.axon/*.jsonl` (user's choice whether
  to version drafts or not - not blocking).
- `pb doctor` reports persistent backlog in `pending/` and size of
  `quarantine/` ([dec-114](dec-114-doctor-diagnostic-first.md)).
- Existing SessionStore tests may need adjustment (mocks that assumed
  rollback journal).
- Accepted as residual risk: filesystems without atomic rename (some
  FUSE) are not supported for pending path - documented in
  `SUPPORT_MATRIX.md`.
- Accepted as residual risk: pending dir can accumulate if
  SessionStore is down indefinitely - doctor reports it, self-heals
  on next drain.
