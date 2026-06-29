# dec-121 Phase 1 — Retire Qdrant (pgvector-only + drop Mem0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `pgvector` the only vector backend and remove every Qdrant code path and the dead Mem0 integration, so the runtime no longer imports `qdrant-client` or `mem0ai` and the Qdrant container can be torn down — gated by the dec-121 recall guard staying green on real data.

**Architecture:** All vector callers already go through one chokepoint, `make_vector_store()` (`src/axon/store/vector_store_factory.py`), and `PgVectorStore` already implements the identical interface to the Qdrant `VectorStore` (`ensure_collections`, `upsert`, `upsert_batch`, `search`, `delete_by_file`, `close`). Phase 1 = (1) extract the shared ranking/size helpers that `pg_vector_store.py` currently imports *from* the Qdrant module, (2) collapse the factory + runtime config to pgvector-only, (3) delete the Qdrant `VectorStore`, (4) drop the orphaned Mem0 stack, (5) run the recall guard on real data as the acceptance gate. Graph, Redis, and the relational SQLite stores are explicitly out of scope (Phases 2 and 3).

**Tech Stack:** Python 3.11+, `asyncpg` + `pgvector`, Typer (`pb` CLI), pytest + `testcontainers.postgres`. Removes `qdrant-client` (transitively) and `mem0ai` from the dependency surface. No new dependencies.

## Global Constraints

- `pgvector` is the ONLY vector backend after this phase. `make_vector_store()` always returns `PgVectorStore`; no `qdrant` branch, no `qdrant_url`.
- `PgVectorStore`'s public interface is the contract and is unchanged: `ensure_collections()`, `upsert(chunk)`, `upsert_batch(chunks)`, `search(...)`, `delete_by_file(ctx, file_path)`, `close()`. Callers (`mcp/server.py`, `pb.py`) must not change behaviour.
- The recall guard (`tests/recall/`, `baseline.json`, `golden_set.json`, gated by `AXON_RUN_RECALL=1`) MUST stay green across this phase — it is dec-121's binding acceptance criterion. No edits that weaken or skip it.
- DO NOT touch: GLYPH / graph retrieval, the Redis `GraphStore` (`dep:*`/`subgraph:*`), the relational SQLite/PG repositories, `SessionStore`. Those are Phases 2–3.
- `memory/session_compressor.py` and `memory/session_hook.py` are NOT Mem0 (they use `litellm` / write daily notes). PRESERVE them — only the Mem0 files leave.
- Validation commands prefix with `rtk` (e.g. `rtk pytest tests/ -q`, `rtk ruff check`). Python 3.11+, `asyncio_mode="auto"` is configured (async tests need no `@pytest.mark.asyncio`).
- Surgical changes only. Every changed line traces to "remove Qdrant/Mem0" or "keep pgvector working".

---

### Task 1: Extract shared vector helpers out of the Qdrant module

**Why first:** `src/axon/store/pg_vector_store.py:9` does `from axon.store.vector_store import VECTOR_SIZE, _rank_and_limit`. `vector_store.py` is the Qdrant module we will delete in Task 3. Deleting it first would break pgvector. So move the shared, backend-agnostic helpers into a neutral module now.

**Files:**
- Create: `src/axon/store/vector_common.py`
- Modify: `src/axon/store/vector_store.py` (re-export from the new module so the Qdrant class keeps working until Task 3 deletes it)
- Modify: `src/axon/store/pg_vector_store.py:9` (import from `vector_common`)
- Test: `tests/store/test_vector_common.py`

**Interfaces:**
- Produces: `vector_common.VECTOR_SIZE: int`, `vector_common._rank_and_limit(...)` — the SAME objects currently in `vector_store.py`. Whatever signature `_rank_and_limit` currently has, it is moved verbatim. Read `src/axon/store/vector_store.py:163` to copy the exact body and signature; read the top of `vector_store.py` to copy how `VECTOR_SIZE` is derived (the embedder-size logic).

- [ ] **Step 1: Read the current definitions**

Read `src/axon/store/vector_store.py` fully. Identify the exact source of `VECTOR_SIZE` (line ~?) and the full body of `def _rank_and_limit(` (line 163). Note any module-level imports those two depend on (e.g. the embedder size lookup, `AXON_VECTOR_SIZE`). You will move those dependencies too.

- [ ] **Step 2: Write the failing test**

```python
# tests/store/test_vector_common.py
from axon.store.vector_common import VECTOR_SIZE, _rank_and_limit


def test_vector_size_is_a_positive_int():
    assert isinstance(VECTOR_SIZE, int) and VECTOR_SIZE > 0


def test_rank_and_limit_is_importable_and_callable():
    assert callable(_rank_and_limit)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `rtk pytest tests/store/test_vector_common.py -v`
Expected: FAIL with `ModuleNotFoundError: axon.store.vector_common`

- [ ] **Step 4: Create `vector_common.py` by moving the helpers**

Move `VECTOR_SIZE` (and its derivation) and the entire `_rank_and_limit` function verbatim from `vector_store.py` into `src/axon/store/vector_common.py`, along with any imports they need. Do not change their logic.

- [ ] **Step 5: Repoint the two importers**

In `src/axon/store/pg_vector_store.py:9`, change the import to:
```python
from axon.store.vector_common import VECTOR_SIZE, _rank_and_limit
```
In `src/axon/store/vector_store.py`, replace the now-moved definitions with a re-export so the Qdrant class still resolves them until Task 3:
```python
from axon.store.vector_common import VECTOR_SIZE, _rank_and_limit  # noqa: F401  (Qdrant class still uses these; deleted in Task 3)
```

- [ ] **Step 6: Run tests to verify green (new + the pgvector recall path)**

Run: `rtk pytest tests/store/test_vector_common.py tests/recall/test_recall_pgvector_path.py -v`
Expected: PASS (the mock-engine pgvector recall path proves `PgVectorStore` still ranks correctly through the relocated helper).

- [ ] **Step 7: Lint + commit**

```bash
rtk ruff check src/axon/store/vector_common.py src/axon/store/pg_vector_store.py src/axon/store/vector_store.py tests/store/test_vector_common.py
git add -A && git commit -m "refactor(store): extract VECTOR_SIZE/_rank_and_limit to vector_common (unblocks Qdrant removal)"
```

---

### Task 2: Collapse the factory and runtime config to pgvector-only

**Files:**
- Modify: `src/axon/store/vector_store_factory.py`
- Modify: `src/axon/config/runtime.py` (remove `qdrant_url` field + read at line 678; remove `"qdrant"` from `_VALID_VECTOR_BACKENDS` at line 143; simplify `_resolve_vector_backend` at line 187)
- Test: `tests/store/test_vector_store_factory.py`, `tests/config/test_runtime.py` (add cases; create the factory test file if absent)

**Interfaces:**
- Consumes: `PgVectorStore(dsn=...)` (existing), `vector_common` (Task 1).
- Produces: `make_vector_store(runtime=None) -> PgVectorStore` (always pgvector).

- [ ] **Step 1: Write the failing tests**

```python
# tests/store/test_vector_store_factory.py
from types import SimpleNamespace

from axon.store.pg_vector_store import PgVectorStore
from axon.store.vector_store_factory import make_vector_store


def test_factory_always_returns_pgvector():
    rt = SimpleNamespace(pg_url="postgresql://axon:axon@localhost:5434/axon", vector_backend="pgvector")
    store = make_vector_store(rt)
    assert isinstance(store, PgVectorStore)
```

```python
# add to tests/config/test_runtime.py (or create it)
import pytest

from axon.config.runtime import _resolve_vector_backend


def test_resolve_vector_backend_defaults_pgvector():
    assert _resolve_vector_backend({}) == "pgvector"


def test_resolve_vector_backend_rejects_qdrant(monkeypatch):
    monkeypatch.setenv("AXON_VECTOR_BACKEND", "qdrant")
    with pytest.raises(ValueError):
        _resolve_vector_backend({})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `rtk pytest tests/store/test_vector_store_factory.py tests/config/test_runtime.py::test_resolve_vector_backend_rejects_qdrant -v`
Expected: FAIL — factory still has a qdrant branch and importing `VectorStore`; `_resolve_vector_backend` still accepts `"qdrant"`.

- [ ] **Step 3: Simplify the factory**

Replace the body of `src/axon/store/vector_store_factory.py` with:
```python
from __future__ import annotations


def make_vector_store(runtime=None):
    """Build the vector store. pgvector is the only backend (dec-121 Phase 1)."""
    from axon.config.runtime import load_runtime_config
    from axon.store.pg_vector_store import PgVectorStore

    rt = runtime or load_runtime_config()
    return PgVectorStore(dsn=rt.pg_url)
```

- [ ] **Step 4: Trim the runtime config**

In `src/axon/config/runtime.py`:
- Line 143: `_VALID_VECTOR_BACKENDS = ("pgvector",)`
- Remove the `qdrant_url: str` field (line 90) and the `qdrant_url=os.environ.get("QDRANT_URL", ...)` construction (line 678).
- In `_resolve_vector_backend` (line 187), keep the default `"pgvector"` and the validation against `_VALID_VECTOR_BACKENDS` (now only pgvector), so an explicit `qdrant` raises `ValueError`.
- If the `RuntimeConfig` dataclass default at line 109 is `"qdrant"`, change it to `"pgvector"` for consistency.
- Grep for any other `qdrant_url` reader: `rtk proxy grep -rn "qdrant_url" src/` — there must be none left after this step.

- [ ] **Step 5: Run tests to verify they pass**

Run: `rtk pytest tests/store/test_vector_store_factory.py tests/config/test_runtime.py -v`
Expected: PASS

- [ ] **Step 6: Lint + commit**

```bash
rtk ruff check src/axon/store/vector_store_factory.py src/axon/config/runtime.py tests/store/test_vector_store_factory.py tests/config/test_runtime.py
git add -A && git commit -m "feat(store): pgvector-only vector backend; drop qdrant_url + qdrant from runtime/factory"
```

---

### Task 3: Relocate `Chunk`, repoint importers, retype pipeline, delete the Qdrant `VectorStore`

**Revised scope (discovered during execution):** `vector_store.py` does NOT only hold the Qdrant class. It also defines `class Chunk(BaseModel)` — the backend-agnostic vector-chunk model the embedder produces and BOTH stores consume — which is imported across `src/` and `tests/`. And `embedder/pipeline.py` imports the Qdrant `VectorStore` purely as a type hint. So you cannot just delete the file: first move `Chunk` to `vector_common.py`, repoint every importer, retype the pipeline hint, trim the one Qdrant-only test class, THEN delete `vector_store.py`. Verified facts:
- `Chunk` is at `vector_store.py:23-34` (a `pydantic.BaseModel`; needs `BaseModel, Field` and `datetime`). Move it verbatim into `vector_common.py`.
- `COLLECTIONS` (`vector_store.py:20`) and `_utcnow` (`vector_store.py:159`) are used ONLY by the Qdrant class — they die with the file. `pg_vector_store.py` passes `now=datetime.now(UTC)` inline and does NOT use `_utcnow`, so the `_utcnow` copy that Task 1 left in `vector_common.py` is a true orphan — DELETE it from `vector_common.py` in this task.
- `pipeline.py` uses the store via dependency injection (duck-typed); the `VectorStore` import is only the `store: VectorStore` type hint at lines ~140 and ~184. Change those hints to `PgVectorStore` (import from `axon.store.pg_vector_store`).
- `obsidian/importer.py` and `pipeline.py` get their store from `make_vector_store()` / injection; their only hard dependency on `vector_store.py` is `Chunk`.

**Files:**
- Modify: `src/axon/store/vector_common.py` (add `class Chunk`; remove the orphan `_utcnow`)
- Delete: `src/axon/store/vector_store.py`
- Modify (repoint `Chunk` / `VECTOR_SIZE` imports from `vector_store` → `vector_common`):
  `src/axon/benchmark/recall.py` (lines ~252, ~364), `src/axon/embedder/pipeline.py` (line ~15, and the `VectorStore` type hint at ~16/140/184), `src/axon/obsidian/importer.py` (line ~113)
- Modify tests (repoint imports): `tests/embedder/test_parse_once.py` (~110), `tests/recall/test_recall_pgvector_path.py` (~34), `tests/store/test_pg_vector_store.py` (~36, ~67, ~101), `tests/store/test_rank_and_limit.py` (~18, ~28)
- Modify: `tests/store/test_stores.py` (remove the `from axon.store.vector_store import VectorStore` import at line ~21 and the Qdrant-only `TestVectorStoreSearch` class + its section comment; KEEP every other test in the file)

- [ ] **Step 1: Move `Chunk` into `vector_common.py`; drop the orphan `_utcnow`**

Copy `class Chunk(BaseModel)` verbatim from `vector_store.py:23-34` into `vector_common.py` (ensure `from pydantic import BaseModel, Field` and `datetime` are imported there). Delete the `def _utcnow()` block from `vector_common.py` (it is unused — `pg_vector_store` inlines `datetime.now(UTC)`).

- [ ] **Step 2: Repoint every importer (src + tests)**

For each file listed under **Files**, change `from axon.store.vector_store import <names>` to `from axon.store.vector_common import <names>` (for `Chunk`, `VECTOR_SIZE`, `_rank_and_limit`). In `pipeline.py`, also replace the `from axon.store.vector_store import VectorStore` and the `store: VectorStore` annotations with `from axon.store.pg_vector_store import PgVectorStore` and `store: PgVectorStore`. In `tests/store/test_stores.py`, delete the Qdrant `VectorStore` import and the `TestVectorStoreSearch` class.

- [ ] **Step 3: Prove `vector_store.py` is now unreferenced, then delete it**

Run: `rtk proxy grep -rn "axon.store.vector_store import\|axon\.store\.vector_store\b" src/ tests/`
Expected: ZERO hits (note: `axon.store.vector_store_factory` and `axon.store.vector_common` are DIFFERENT modules and are fine; the regex word-boundary excludes them). If any hit remains, repoint it before deleting.
Then: `git rm src/axon/store/vector_store.py`

- [ ] **Step 4: Verify imports + the touched suites are green**

Run: `rtk python3 -m compileall src/axon` (no import errors), then
`rtk pytest tests/store tests/embedder tests/recall/test_recall_pgvector_path.py tests/obsidian -q`
Expected: PASS, no `ModuleNotFoundError: axon.store.vector_store`. (Suites needing a Postgres container will spin one; if the container cannot start, report DONE_WITH_CONCERNS with the compileall + non-container results and name the blocked tests — do not fake them.)

- [ ] **Step 5: Lint + commit (explicit paths, NEVER `git add -A`)**

```bash
rtk ruff check src/axon/store/vector_common.py src/axon/benchmark/recall.py src/axon/embedder/pipeline.py src/axon/obsidian/importer.py
git add src/axon/store/vector_common.py src/axon/store/vector_store.py src/axon/benchmark/recall.py src/axon/embedder/pipeline.py src/axon/obsidian/importer.py tests/embedder/test_parse_once.py tests/recall/test_recall_pgvector_path.py tests/store/test_pg_vector_store.py tests/store/test_rank_and_limit.py tests/store/test_stores.py
git commit -m "feat(store): relocate Chunk to vector_common, delete Qdrant VectorStore (pgvector is the only backend)"
```
(`git rm` already staged the deletion; the explicit `git add` stages the edits. Do NOT use `git add -A` — `data/compression/stats.jsonl` must stay unstaged.)

---

### Task 4: Drop the orphaned Mem0 stack

**Context:** Mem0 is dead (only `pb memory smoke` + an `axon_health` presence probe reach it; the `recall_context` `semantic_search` seam is never wired). Remove it and the `mem0ai` dependency. PRESERVE `memory/session_compressor.py` and `memory/session_hook.py` (not Mem0). Keep the `recall/strategy.py` `semantic_search` extension point (a clean seam; comments mentioning mem0 are docs only).

**Files:**
- Delete: `src/axon/memory/mem0_tool.py`, `src/axon/memory/config.py`
- Modify: `src/axon/memory/__init__.py` (drop any mem0 export; keep the package for the two preserved modules)
- Modify: `src/axon/cli/pb.py` (remove `memory_app` at line 32, `app.add_typer(memory_app, name="memory")` at line 47, and the `memory_smoke` command at lines 2744–2766)
- Modify: `src/axon/mcp/server.py` (remove the mem0 presence check at lines 1042–1046 and the `- mem0: ...` line in the `axon_health` output near line 1001)
- Modify: `src/axon/__main__.py` (update the `health` docstring at line ~198 if it mentions mem0)
- Modify: `pyproject.toml` (remove `"mem0ai>=0.1.0"` at line 38)
- Delete tests: `tests/cli/test_pb_cli.py::test_memory_smoke_uses_mem0_helpers` (lines ~1459–1471); in `tests/config/test_runtime_expansion.py` remove the `Mem0Config` import (line 13) and its two tests (lines ~38–53)

- [ ] **Step 1: Write the failing guard test**

```python
# tests/test_no_mem0.py
import importlib

import pytest


def test_mem0_modules_are_gone():
    for mod in ("axon.memory.mem0_tool", "axon.memory.config"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(mod)


def test_preserved_memory_modules_still_import():
    importlib.import_module("axon.memory.session_compressor")
    importlib.import_module("axon.memory.session_hook")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `rtk pytest tests/test_no_mem0.py -v`
Expected: FAIL — `mem0_tool`/`config` still import successfully.

- [ ] **Step 3: Delete the Mem0 files and callsites**

Delete the two Mem0 modules and apply every modification listed under **Files** above. After editing, grep to confirm nothing references the removed names:
```bash
rtk proxy grep -rn "mem0_tool\|Mem0Config\|memory_app\|from axon.memory.config" src/ tests/
```
Expected: no hits in `src/`; in `tests/` only the deletions you are making.

- [ ] **Step 4: Run the guard test + the touched suites**

Run: `rtk pytest tests/test_no_mem0.py tests/cli/test_pb_cli.py tests/config/test_runtime_expansion.py -q`
Expected: PASS (the deleted tests are gone; the rest green).

- [ ] **Step 5: Confirm the dependency and a clean import of the app**

Run: `rtk proxy grep -n "mem0" pyproject.toml` → expected: no output.
Run: `rtk python3 -c "import axon.cli.pb; import axon.mcp.server; print('ok')"`
Expected: `ok` (no mem0 import error).

- [ ] **Step 6: Lint + commit**

```bash
rtk ruff check src/axon/cli/pb.py src/axon/mcp/server.py src/axon/__main__.py
git add -A && git commit -m "feat: drop orphaned Mem0 integration + mem0ai dependency (dead since recall seam never wired)"
```

---

### Task 5: Remove the residual Qdrant arm and drop `qdrant-client`

**Discovered during execution:** deleting the Qdrant `VectorStore` (Task 3) did not remove all Qdrant code. `src/axon/benchmark/recall.py` still has a SYNC Qdrant arm (`from qdrant_client import QdrantClient`, `TEMP_COLLECTION`, `index_corpus`, `run_recall_guard`) alongside the pgvector arm (`index_corpus_pg`, `run_recall_guard_pg`, `RECALL_TABLE`), and `tests/recall/test_recall_guard.py` imports `QdrantClient` and dispatches on `AXON_VECTOR_BACKEND` (default `qdrant`). Until that arm is gone, `qdrant-client` cannot be removed. This task removes the Qdrant arm and the dependency, leaving the pgvector arm as the only recall path.

**Files:**
- Modify: `src/axon/benchmark/recall.py` (remove the `qdrant_client` imports, `TEMP_COLLECTION`, `index_corpus`, `run_recall_guard`; KEEP `index_corpus_pg`, `run_recall_guard_pg`, `RECALL_TABLE`; update the module docstring that says "Supports both Qdrant and pgvector")
- Modify: `tests/recall/test_recall_guard.py` (remove the `QdrantClient` import + the Qdrant dispatch branch; make pgvector the only path — default `AXON_VECTOR_BACKEND` to `pgvector` and drop the qdrant `if` branch; remove or migrate the `test_recall_guard_shape` test that exercises the sync `run_recall_guard` Qdrant arm)
- Modify: `pyproject.toml` (remove `"qdrant-client>=1.9.0"`; change `testcontainers[qdrant,redis,postgres]` → `testcontainers[redis,postgres]` — keep `redis`, Phase 2 still needs it)
- Note: `tests/embedder/fixtures/python/vector_store.py` is FIXTURE TEXT (a sample file the chunker parses), NOT importable code — leave it; its `qdrant_client` lines do not import anything at runtime.

- [ ] **Step 1: Prove pgvector is the only live recall arm needed, then remove the Qdrant arm**

Read `src/axon/benchmark/recall.py` and `tests/recall/test_recall_guard.py`. Remove the Qdrant sync arm from both (imports, `TEMP_COLLECTION`, `index_corpus`, `run_recall_guard`, the `QdrantClient` dispatch branch, the Qdrant-only shape test). Keep the pgvector arm intact.

- [ ] **Step 2: Prove no runtime `qdrant_client` import remains**

Run: `rtk proxy grep -rn "qdrant_client\|AsyncQdrantClient\|from qdrant" src/ tests/ | grep -v "fixtures/"`
Expected: ZERO hits (the only allowed remaining mention is the chunker FIXTURE under `tests/embedder/fixtures/`). If any real import remains, remove it before touching `pyproject.toml`.

- [ ] **Step 3: Drop the dependency**

In `pyproject.toml`, delete `"qdrant-client>=1.9.0"` and change `testcontainers[qdrant,redis,postgres]>=4.0.0` to `testcontainers[redis,postgres]>=4.0.0`.

- [ ] **Step 4: Verify imports + recall tests collect and pass**

Run: `rtk python3 -m compileall src/axon/benchmark` then
`rtk pytest tests/recall/ -q` (the mock-engine pgvector path runs without `AXON_RUN_RECALL`).
Expected: PASS, no `ModuleNotFoundError: qdrant_client`.

- [ ] **Step 5: Lint + commit (explicit paths, NEVER `git add -A`)**

```bash
rtk ruff check src/axon/benchmark/recall.py tests/recall/test_recall_guard.py
git add src/axon/benchmark/recall.py tests/recall/test_recall_guard.py pyproject.toml
git commit -m "feat(recall): remove residual Qdrant recall arm + drop qdrant-client dependency"
```

---

### Task 6: Recall-guard acceptance gate on real data + Qdrant teardown (operational)

**Files:** None (operational validation + the dec-121 acceptance gate). This task has no code deliverable; it is the gate that promotes dec-121's vector slice from `proposed` to `accepted`.

**Note:** the bare suite has two PRE-EXISTING environmental failures (`test_index_reports_processed_counts`, `test_watch_reindexes_changed_files`) that need `AXON_PG_URL` pointed at the real PG (port 5434) — without it the loader defaults to 5433 (lume-db) and PG auth fails. These are NOT Phase-1 regressions; export `AXON_PG_URL` for the suite run.

- [ ] **Step 1: Full suite green without Qdrant/Mem0**

```bash
rtk pytest tests/ -q
```
Expected: PASS. If any test still imports Qdrant or Mem0, it leaked from Tasks 3–4 — fix before proceeding.

- [ ] **Step 2: Run the recall guard against pgvector on the real corpus**

```bash
export AXON_PG_URL="postgresql://axon:axon@localhost:5434/axon"
export AXON_RUN_RECALL=1
rtk pytest tests/recall/test_recall_guard.py -v
```
Expected: PASS with no regression versus `tests/recall/baseline.json`. This exercises `run_recall_guard_pg` / `index_corpus_pg` over `PgVectorStore`. If recall regresses, STOP — tune HNSW (`m`, `ef_construction`, `ef_search`) or refresh the baseline deliberately; do not weaken the gate to pass.

- [ ] **Step 3: Smoke the live vector path through the CLI**

```bash
pb search "decision backfill" 2>&1 | head        # exercises make_vector_store -> PgVectorStore.search
```
Expected: results returned (no Qdrant connection attempt, no import error).

- [ ] **Step 4: Tear down the Qdrant container (operational)**

Only after Steps 1–3 are green:
```bash
docker stop axon-qdrant-1 && docker rm axon-qdrant-1
```
Confirm the MCP server and `pb` still operate (`pb doctor`).

- [ ] **Step 5: Promote the decision**

Mark dec-121 step 2's recall gate as passed: flip `docs/decisions/dec-121-postgres-unified-storage.md` status toward `accepted` for the vector slice (the controller handles the ADR edit separately). Note in the Phase-1 completion that Qdrant + Mem0 are fully removed and pgvector is the sole vector backend.

---

## Self-Review

**Spec coverage:** retire Qdrant from the default runtime → Tasks 2 (factory/runtime) + 3 (delete class) + 5 (container teardown); pgvector as sole backend → Tasks 1–3; drop Mem0 + `mem0ai` → Task 4; recall gate stays green (dec-121's binding criterion) → Task 5 (and exercised in Task 1 Step 6). Out-of-scope (graph, Redis, relational SQLite) is intentionally untasked — Phases 2–3.

**Placeholder scan:** no TBD/TODO. Task 1 instructs reading the exact current definitions before moving them (they are codebase-specific and must be copied verbatim, not invented). Every other code step shows complete code.

**Type consistency:** `make_vector_store(runtime=None) -> PgVectorStore`, `vector_common.VECTOR_SIZE: int`, `vector_common._rank_and_limit(...)` (moved verbatim), `_resolve_vector_backend(dict) -> str`, `_VALID_VECTOR_BACKENDS = ("pgvector",)` are used consistently across tasks. `PgVectorStore`'s interface (`ensure_collections`/`upsert`/`upsert_batch`/`search`/`delete_by_file`/`close`) is unchanged.

**Known follow-ups (out of scope):** Phase 2 (port Redis `dep:*` → `symbol_deps` PG table, delete the dead `subgraph:*` cache, fix the `graph_source.py` mtime-on-SQLite cache invalidation under PG). Phase 3 (fix the 6 SQLite-bypass callsites, port/drop `FailureStore`/`OutcomeStore`, delete the SQLite repos + `aiosqlite`). The `recall/strategy.py` `semantic_search` seam is retained as the future pgvector-semantic-recall extension point.
