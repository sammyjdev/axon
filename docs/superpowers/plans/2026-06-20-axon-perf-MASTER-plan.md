# AXON Indexing Perf Overhaul - MASTER Execution Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Read THIS master doc first - it is authoritative and overrides any conflicting detail in the three sub-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make AXON code indexing fast, memory-safe, cacheable, and linear - leveraging the desktop GPU - without regressing search recall.

**Architecture:** Three sub-plans, executed in order **A -> B -> C**. This master doc fixes the shared interfaces, assigns single ownership to each shared piece (so nothing is built twice), and lists the residual fixes each sub-plan needs. The detailed bite-sized TDD steps live in the three sub-plan files; this doc is the coordination layer.

**Tech Stack:** Python 3.11, fastembed (bge-base ONNX), onnxruntime / onnxruntime-gpu (CUDA), tree-sitter, Qdrant, Redis, SQLite, pytest + testcontainers.

## Global Constraints

- Output rule: only the plain hyphen `-`, never em/en dashes.
- TDD per task; commit frequently; DRY; YAGNI.
- **No change may regress the recall guard** (see Foundation F1): Top-1 >= 0.90 AND no per-query regression vs `tests/recall/baseline.json`.
- Per-machine dependency: desktop = `onnxruntime-gpu` + nvidia CUDA libs; M1 Pro mac = `onnxruntime` + CoreML. **Do NOT pin `onnxruntime-gpu` in `pyproject.toml`** - document it in `docs/gpu-setup.md`.
- Secrets / gitignored files must NEVER be embedded (enforced by the walk + a test).

---

## Phase 0 - DONE (gate satisfied)

The measurement gate is complete and committed at `benchmarks/phase0_baseline.json`. Key results that drive this plan (do NOT re-run a Phase 0 task - it is closed):

- **GPU is the dominant, proven lever:** RTX 4070 Ti embeds the real corpus at **541 chunks/s** vs **~4/s** CPU (~135x); full 9-repo reindex ~9s on GPU. Recipe validated: `onnxruntime-gpu==1.26.0` + `nvidia-cudnn-cu12` + `nvidia-cublas-cu12` + `nvidia-cuda-runtime-cu12` + `ort.preload_dlls()` + `providers=['CUDAExecutionProvider','CPUExecutionProvider']`. **Always verify** `model.model.model.get_providers()` to catch silent CPU fallback.
- **14GB RSS root cause = onnxruntime CPU activation arena** (batch x sequence), NOT the `graph_chunks` list (135MB). On GPU it lives in VRAM. So the chunk-cap + token-budget batching are **memory-safety / recall / CPU-fallback** measures, not the primary perf lever.
- onnxruntime intra_op threads default `0` = auto = all cores -> **no thread-tuning task**.
- rglob 0.34s vs git ls-files 0.019s -> the walk change is a **security** fix (exclude gitignored), not perf.
- 0 `_chunk_id` collisions today -> the D1 migration is safe. All data sits in `personal`; `knowledge` is empty -> clean purge + reindex.

---

## Canonical shared interfaces (AUTHORITATIVE - override the sub-plans)

The three sub-plans drifted on these. Use EXACTLY these signatures everywhere; ignore any other variant in A/B/C.

1. **D1 stable chunk-id** - implemented ONCE in `src/axon/embedder/pipeline.py`, owner **Plan A**:
   ```python
   def _chunk_id(file_path: str, symbol: str, occurrence_index: int) -> str:
       return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{file_path}::{symbol}::{occurrence_index}"))
   ```
   - `occurrence_index` = 0-based count of how many chunks with that same `symbol` were already seen IN THIS FILE during the walk (disambiguates overloads and split sub-chunks).
   - Call sites pass `str(path)`, `chunk.symbol`, and a per-`(file, symbol)` counter. Example at every call site:
     ```python
     _occ: dict[str, int] = {}
     for c in chunks_of_this_file:
         i = _occ.get(c.symbol, 0); _occ[c.symbol] = i + 1
         cid = _chunk_id(str(file_path), c.symbol, i)
     ```
   - Both call sites MUST be updated: `index_path` (~pipeline.py:173) and `ingest_file` (~pipeline.py:109). The second arg is `c.symbol` (a `str`), NOT a `Chunk` object.

2. **Provider detection** - implemented ONCE in `src/axon/embedder/engine.py`, owner **Plan B**, named with a leading underscore:
   ```python
   def _detect_providers() -> list[str]: ...
   ```
   Calls `onnxruntime.preload_dlls()` (guarded by `hasattr`) then returns, in priority: CUDA -> CoreML -> CPU. `EmbedderEngine` passes `providers=_detect_providers()` to `TextEmbedding` and verifies the bound providers.

3. **Existing delete** - reuse `vector_store.delete_by_file(ctx: str, file_path: str)` (vector_store.py:163); for all-context delete loop `for ctx in COLLECTIONS:`. Do NOT add a new delete method.

4. **file_index table** - owner **Plan C**: columns `file_path TEXT, ctx TEXT, sha1 TEXT, status TEXT, chunk_count INTEGER, indexed_at TEXT, PRIMARY KEY (file_path, ctx)`. `FileCache`: `get_all_sha1s(ctx)->dict[str,str]` (status='done' only), `set_entry(file_path, ctx, sha1, chunk_count, *, status='done')`, `delete_entry(file_path, ctx)`. All `file_path` normalized via `Path(p).as_posix()`. **The `ctx` used for a file must be identical across its `pending` and `done` writes** (use the per-file ctx, never a default).

5. **File walk** - owner **Plan A**, new module `src/axon/repo/file_walk.py`, `iter_git_files(repo)`:
   - `git ls-files --cached` (tracked only; NO `--others`), then exclude any path that `git -C <root> check-ignore --stdin -z` reports (use `-z` null-delimited; parse on `\0`, NOT `splitlines()`).
   - Define a LOCAL `_EXCLUDED_DIR_NAMES` constant inside `file_walk.py` (copy the set); do NOT `from axon.embedder.pipeline import EXCLUDED_DIR_NAMES` (circular import).

6. **File hash** - keep identical to today: `hashlib.sha1(source.encode("utf-8")).hexdigest()` (pipeline.py:161). If `usedforsecurity=False` is ever added, add it to BOTH sites in the same PR.

---

## Execution order + ownership (build each shared piece ONCE)

Run the sub-plans in this order. Within each, BUILD only what it owns; SKIP the listed tasks that another plan owns.

### 1. Plan A - `2026-06-20-axon-perf-A-chunking-linear-walk-plan.md`
Owns and builds: **F1 recall guard** (golden set + harness + `baseline.json`), **D1** `_chunk_id`, **chunk-size cap** (incl. markdown-by-section + `_split_lines_into_chunks`), **parse-once** linearization, **git walk** (`iter_git_files`). Execute all its tasks. Apply Plan A residual fixes below.

### 2. Plan B - `2026-06-20-axon-perf-B-embedding-acceleration-plan.md`
Owns and builds: **GPU provider detection** (`_detect_providers` + preload_dlls + bound verification), **token-budget batching** (embedding-side memory safety), `docs/gpu-setup.md`.
- **SKIP** Plan B Task 1 (recall guard) - already built by Plan A (F1). Reuse `tests/recall/`.
- **SKIP** the chunk-cap / `_split_large_chunk_by_lines` parts of Plan B Task 3 - owned by Plan A. Keep ONLY the token-bounded batching (`_estimate_tokens`, `_make_token_bounded_batches`).
- Apply Plan B residual fixes below.

### 3. Plan C - `2026-06-20-axon-perf-C-incremental-cache-plan.md`
Owns and builds: **file_index** cache + `003_file_index.sql` migration, **pending-sentinel crash-safety (D2)**, **per-file reconcile (D4/D6)** via `delete_by_file`, **lockfile** with PID staleness, **pipelined Redis** dep upserts, **one-shot blue/green migration** of the 9 repos.
- **SKIP** Plan C Task 1 (recall guard) - reuse Plan A's F1.
- **SKIP** the D1 `_chunk_id` change and the git-walk change inside Plan C Task 6 - owned by Plan A; consume them.
- Apply Plan C residual fixes below.

---

## Residual fixes to apply during execution (from adversarial review)

### Plan A
- [ ] Task 3 references `_split_lines_into_chunks` (defined in Task 5). REORDER: implement Task 5's `_split_lines_into_chunks` BEFORE wiring it in Task 3 (or merge Tasks 3+5). No forward-reference stubs.
- [ ] `iter_git_files` check-ignore call MUST use `git check-ignore --stdin -z` and parse on `\0` (security: paths with spaces).
- [ ] `file_walk.py` defines its own `_EXCLUDED_DIR_NAMES` (no import from pipeline) - circular-import fix.
- [ ] `test_chunk_id_stable_across_line_shift` must construct two chunks with the SAME `(symbol, occurrence_index)` but DIFFERENT `start_line` and assert `_chunk_id` returns the SAME uuid (that is the point of D1). The current draft calls it twice with identical args (tests determinism, not stability).
- [ ] Use the canonical `_chunk_id(file_path: str, ...)` signature (the draft widened to `str | Path`; narrow to `str`, call sites already pass `str(...)`).

### Plan B
- [ ] Rename `detect_providers` -> `_detect_providers` everywhere (engine.py, tests, docs, E2E).
- [ ] `index_path` `_chunk_id` call sites: use the 3-arg canonical form with an occurrence counter (the draft emitted the old 2-arg `_chunk_id(file_path, c)`).
- [ ] Wire the LIVE recall harness into `test_no_regression` (it is currently a `score >= 0.0` stub). After Plan A's F1 exists, this test runs `run_recall_harness()` -> `compare_benchmark_runs(current, baseline)` -> assert `len(report.regressions) == 0` and `current.score >= 0.90`.
- [ ] The unit/integration tests the review flagged as "missing from B" that are actually owned by A or C (chunk-id, git, reconcile, sentinel) are NOT B's - they live in A/C. Only `test_idempotencia_provider_fallback` (provider re-detection is idempotent) belongs to B - add it.

### Plan C
- [ ] `golden_set.json` / `score_calibration.json` placeholders: these are PRODUCED by running Plan A's F1 harness + a one-time calibration on both machines - they are NOT committed as final guesses. F1 (Plan A) owns golden_set.json; Plan C only consumes it. Remove C's duplicate golden_set creation.
- [ ] Add `tests/embedder/test_orphan_reconcile.py` (index -> edit a file -> re-index -> scroll Qdrant; assert point count is identical, not accumulated) with real code.
- [ ] Add `tests/embedder/test_cross_ctx_no_false_positive.py` (a file in `knowledge` is not deleted from Qdrant when indexing `work`) with real code. Build `found_paths` per-`(file_path, ctx)` and compare scoped to the same ctx.
- [ ] The "Phase 0 gate" precondition in Plan C is ALREADY SATISFIED (`benchmarks/phase0_baseline.json` is committed). Do not add a Phase 0 task; reference the committed baseline.
- [ ] Use the canonical `_chunk_id(file_path, c.symbol, occ)` (symbol str), NOT a `Chunk` object.
- [ ] ctx consistency: write `pending` AND `done` with the SAME per-file `ctx` (the draft used a `file_ctx_default` for the done write - bug). Track `(file_path, ctx)` in `pending_file_meta` and use it in the post-flush done write.

---

## Handoff

Execute in a fresh chat with `superpowers:subagent-driven-development`: dispatch one subagent per task, review between tasks. Order: **Plan A (all tasks) -> Plan B (its owned tasks) -> Plan C (its owned tasks)**, applying the residual fixes above. After each sub-plan, run the recall guard (F1) and assert no regression before moving on.
