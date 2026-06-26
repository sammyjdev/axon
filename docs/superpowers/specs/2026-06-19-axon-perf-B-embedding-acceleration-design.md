# Design: Embedding Acceleration (Spec B - Performance Pillar)

Date: 2026-06-19
Status: draft - awaiting measurement gate (Phase 0)
Scope: **B** (embedding). Pillar "incremental cache" = Spec C (file_index SQLite, reconcile, lockfile). Pillar "parse-once / chunk-cap" = Spec A (chunk-size cap, parse-once linearization, git ls-files walk). This spec covers: auto-detected provider per machine, token-bounded batching, onnxruntime thread tuning, stable chunk-id, crash-safety via pending sentinel, indexing scope restricted to git-tracked files. Acceleration only after measurement proves necessity (YAGNI).

---

## Context

AXON uses `fastembed v0.8.0` with `TextEmbedding` without any `providers`
argument (engine.py:56-62). onnxruntime selects the provider by default - in practice, only
`CPUExecutionProvider` is available today on the desktop with RTX 4070 Ti, because the installed
wheel is the CPU-only build (hypothesis to verify - see Assumptions). On Mac M1 Pro,
CoreML is also not passed explicitly.

Verified facts in the code:
- `engine.py:23-26` - platform detection: `Darwin+arm64` -> bge-small (384-dim, ~33 MB);
  all others -> bge-base (768-dim, ~110 MB). No GPU detection.
- `engine.py:56-62` (`_ensure_model`) - `TextEmbedding(model_name=..., cache_dir=...)`;
  no `providers` argument passed.
- `engine.py:17-20` (`FASTEMBED_MODEL_DIMS`) - static dimension map; clean, no refactor needed.
- `pipeline.py:29` - `_BATCH_SIZE = 400` (chunks per Qdrant flush).
- `pipeline.py:170` - `engine.embed([c.content for c in chunks])` called per file;
  real batches are 5-30 chunks per typical file, not 400.
- `pipeline.py:28` - `_FILE_HASH_CACHE: dict[str, str] = {}` - in-process, does not persist
  between runs (issue handled in Spec C via file_index SQLite, referenced here only as context).
- `pipeline.py:141` - `graph_chunks: list[Chunk] = []` accumulates all chunks from all
  files before calling `build_dependency_records` (line 197); HYPOTHESIS: possibly
  responsible for the observed RSS spike (see P3 - hypothesis to verify in Phase 0).
- `pipeline.py:206-211` - `_chunk_id` uses `f"{path}::{chunk.symbol}::{chunk.start_line}"`,
  which creates unstable ids: editing lines above a symbol changes its id and creates orphan
  points in Qdrant. Fixed via D1 (uuid5 with occurrence_index).
- `vector_store.py:24` - `COLLECTIONS = list(VALID_CONTEXTS)` - existing constant.
- `vector_store.py:163` - `delete_by_file(ctx, file_path)` - existing method; will be
  reused (D4); no new delete method will be created.

Preliminary measurement (1 sample, machine under load; not a definitive baseline):
- fastembed model load: ~0.6 s
- Short chunks (small functions): ~240 chunks/s
- Long chunks (~300 tokens): ~3 chunks/s

These numbers show throughput is dominated by chunk size, not I/O.
Spec A for init-code-embedding (2026-06-19-axon-init-code-embedding-design.md) already
identified memory and throughput risk as "dedicated perf pass - to confirm".
This spec is that pass.

---

## Assumptions (ledger - verify before any code changes)

| # | Assumption | Cheap verification | Consequence if false |
|---|---|---|---|
| P1 | RTX 4070 Ti present but onnxruntime installed without CUDA (CPU-only wheel) | `python -c "import onnxruntime as ort; print(ort.get_available_providers())"` + `pip show onnxruntime onnxruntime-gpu` on desktop. If `CUDAExecutionProvider` absent, confirmed. | If CUDA already available: skip wheel swap, go directly to passing `providers` kwarg. |
| P2 | `fastembed 0.8.0` accepts `providers` kwarg in `TextEmbedding()` | `python -c "from fastembed import TextEmbedding; import inspect; print(inspect.signature(TextEmbedding.__init__))"` + check changelog 0.8.0. | If absent: requires version upgrade or injection via `SessionOptions` from onnxruntime directly. |
| P3 | The RSS spike observed during indexing may come from the `graph_chunks` list accumulating all chunks before `build_dependency_records` (pipeline.py:141-196), from other factors such as the model load itself, or from both | RSS snapshot with `psutil` before/after `graph_chunks.extend()` (pipeline.py:187) and before/after `_ensure_model()` on a medium repo (~1,000 files). Record in `benchmarks/phase0_profile.json`. | If the model is at fault: the solution is to unload the model after embedding, not to stream graph_chunks. Hypothesis not confirmed until Phase 0 completes. |
| P4 | onnxruntime on R7 5800X3D uses fewer than the 16 available threads (idle cores during embed) | `psutil.cpu_percent(percpu=True)` in parallel with an index. Check default of `ort.SessionOptions().intra_op_num_threads`. | If already using all cores: thread tuning gains nothing. |
| P5 | GPU (if available via CUDAExecutionProvider) is faster than CPU for AXON's real batch sizes (5-30 chunks per typical file) | WITHOUT measuring do not assert. After wheel swap: compare `providers=['CUDAExecutionProvider','CPUExecutionProvider']` vs `['CPUExecutionProvider']` with the synthetic corpus of 500 functions. GPU only wins if it amortizes PCIe transfer overhead for these batch sizes. For small batches, GPU frequently loses due to PCIe launch overhead; cross-file batching may be necessary for GPU to be advantageous - that is out of scope for this spec. | If CPU is faster for small batches: do not use GPU by default for small files; use larger batching to activate GPU. |
| P6 | `rglob` without directory pruning is a relevant contributor to total wall time | `time python -c "from pathlib import Path; import time; t=time.perf_counter(); files=list(Path('<vault>').rglob('*')); print(len(files), time.perf_counter()-t)"` vs `time git ls-files --cached | wc -l`. | If rglob < 1 s: bottleneck is embedding, not I/O (pruning becomes YAGNI). |

---

## Success criteria (numeric, per machine)

| Metric | Target R7 5800X3D | Target M1 Pro | How to measure |
|---|---|---|---|
| Full-index wall time (9 repos, cold hash-cache) | <= 5 min | <= 8 min | `time axon index <vault_root>` in a fresh process; median of 3 runs; no model pre-warm |
| Incremental refresh (1 changed file, 10-50 chunks) | <= 10 s | <= 15 s | Change 1 already-indexed .py file, measure wall time of `axon index <repo>`; 5 files of varying sizes (10/20/30/40/50 chunks); all must pass |
| Post-commit hook (20 .py/.java files) | <= 30 s | <= 45 s | Commit touching 20 files; measure wall time of `python -m axon.hooks.git_event post-commit`; 3 runs, take maximum |
| Peak RSS during full index (9 repos) | <= 2 GB | <= 1.5 GB | `psutil.Process().memory_info().rss` sampled every 2 s; model size counts (~110 MB desktop, ~33 MB mac) |
| Embedding throughput (chunks/s end-to-end: chunk+embed+upsert) | defined after Phase 0 (baseline ~240 chunks/s short; conditional target on GPU vs CPU-only - see note below) | >= 200 chunks/s | Fixed synthetic corpus of 500 Python functions (15-30 lines each); `total_chunks / wall_seconds` |
| Recall Top-1 (query -> correct file) | >= 0.90 on golden set of 20 queries | >= 0.90 | See "Quality guard" section below |
| Recall Top-3 (correct file in first 3 hits) | >= 0.95 on golden set | >= 0.95 | Same harness |
| Exclusion of gitignored files (security) | 0 Qdrant points with `file_path` from a gitignored file | 0 points | After indexing repo with `.env` and `secrets.json` in .gitignore, scroll Qdrant and assert empty |
| Chunk-id correctness after per-file reconcile | 0 orphan points after editing 3 lines above a symbol | 0 points | See "Per-file Reconcile" section |

**Note on desktop throughput target**: the only available measurement is ~240 chunks/s
(short chunks, 1 sample, machine under load). The final numeric target for the desktop will be fixed
after Phase 0 with a baseline under controlled conditions. If GPU is available and the gain
is confirmed (P5), a higher target is justified. In CPU-only mode, the target is
defined as a measurable improvement over the Phase 0 baseline, not as an absolute number of 300/s.

---

## Measurement gate - Phase 0 (blocking)

**No changes to indexing code are permitted until all conditions below are true.**

### Gate conditions (all must be met)

1. **Throughput baseline captured**: run `index_path` on the synthetic corpus of 500 functions
   on both machines; record chunks/s and wall time in `benchmarks/phase0_baseline.json`.

2. **Peak RSS baseline captured**: run full index of the 9 repos on both machines with
   `psutil` sampled every 2 s; record peak in `benchmarks/phase0_baseline.json`.
   - **Emergency block**: if RSS > 8 GB on desktop, run the P3 probe immediately:
     measure RSS before/after `graph_chunks.extend()` (pipeline.py:187) and before/after
     `_ensure_model()` to identify which component is dominant. If hypothesis P3
     is confirmed (`graph_chunks` list is responsible), implement streaming of
     `build_dependency_records` per file (Spec C, pipeline.py:197) as item 0 of Phase 1, before
     any embedding optimization. Note: per-file streaming would lose cross-file `called_by` edges
     that `build_dependency_records` aggregates when receiving all chunks together; that trade-off is Spec C's responsibility to evaluate.

3. **Bottleneck identified and ranked**: run probes for assumptions P4 (threads), P6
   (rglob), P3 (RSS) and "large chunks" on desktop; record raw numbers in
   `benchmarks/phase0_profile.json`. At least one bottleneck confirmed with a measured number.
   Exit condition: `benchmarks/phase0_profile.json` contains non-null values for
   `ort_default_threads`, `rglob_wall_sec`, `large_chunks_found` and RSS annotation before/after
   the `graph_chunks` extension.

4. **GPU availability confirmed or ruled out**: run P1 and P2 probes on
   desktop; record in `benchmarks/phase0_baseline.json` as `"desktop_gpu_available":
   true/false`. If `false`, GPU is removed from the plan and Phase 1 covers CPU threading only.

5. **Stale Qdrant points confirmed**: on an already-indexed repo, run:
   ```python
   from qdrant_client import QdrantClient
   client = QdrantClient("http://localhost:6333")
   # Edit 3 lines above a symbol without changing the symbol
   # Re-index the file
   # Count points with the file's file_path
   result = client.scroll(collection_name="knowledge",
                          scroll_filter=Filter(must=[FieldCondition(
                              key="file_path", match=MatchValue(value="<test_file>"))]),
                          limit=100)
   print(len(result[0]))  # must be == number of symbols in the file, no duplicates
   ```
   Record in `benchmarks/phase0_baseline.json` as `"stale_qdrant_points_confirmed": bool`.
   If `true`, stable chunk-id (D1) + per-file reconcile (D6) become a priority item in Phase 1.

6. **Recall baseline captured**: run the 20-query harness against the current index
   (without any changes); record `BenchmarkRunSummary` in `tests/recall/baseline.json`.
   Top-1 and Top-3 must be >= 0.80 for a valid baseline. Below that = a pre-existing
   bug to fix before the overhaul.

### Required output from Phase 0

`benchmarks/phase0_baseline.json`:
```json
{
  "desktop_full_index_wall_sec": null,
  "desktop_peak_rss_mb": null,
  "desktop_chunks_per_sec": null,
  "mac_full_index_wall_sec": null,
  "mac_peak_rss_mb": null,
  "mac_chunks_per_sec": null,
  "desktop_gpu_available": null,
  "rglob_wall_sec": null,
  "redis_loop_ms_per_100_symbols": null,
  "ort_default_threads": null,
  "large_chunks_found": null,
  "stale_qdrant_points_confirmed": null,
  "recall_top1_baseline": null,
  "recall_top3_baseline": null
}
```

This file must be committed before any Phase 1 PR is opened.

---

## Design decisions

| Topic | Decision |
|---|---|
| Stable chunk-id (D1) | `_chunk_id` changes to `uuid5(NAMESPACE_URL, f"{file_path}::{symbol}::{occurrence_index}")` where `occurrence_index` is the 0-based index of the symbol within the file (disambiguates overloads and sub-chunks such as `foo[0]/foo[1]`). `start_line` is REMOVED from the id. Editing lines above a symbol does not create orphan points. |
| Crash-safety via pending sentinel (D2) | The `file_index` table receives a `status` column. On re-indexing: (a) write row with `status='pending'` + new sha BEFORE mutating Qdrant; (b) `delete_by_file` + upsert of new points; (c) set `status='done'`. On any run, a `'pending'` row is treated as dirty and re-indexed. One-shot migration of the 9 already-indexed repos uses BLUE/GREEN (new Qdrant collection, recall gate, alias swap only if passed). Normal incremental runs do NOT use blue/green. |
| Walk scope = tracked-only + check-ignore (D3) | Replace `rglob` (pipeline.py:70) with `git ls-files --cached` (WITHOUT --others). Filter each path via `git check-ignore` to exclude files that were committed and later gitignored. Untracked files require `git add` before they can be indexed. Security guarantee: "no gitignored file is ever embedded" - add test (commit .env, add to .gitignore, assert 0 points in Qdrant). Fallback to rglob if not a git repo. |
| Reuse existing delete (D4) | Do NOT add any new delete method. `vector_store.py:163` already has `delete_by_file(ctx, file_path)`. Use via loop over `COLLECTIONS` (vector_store.py:24) for all-context delete. Remove any proposal for `delete_file_points`, `delete_by_file_path`, or `_collections()`. |
| 14 GB is hypothesis, not fact (D5) | The probable cause of the observed RSS spike is the `graph_chunks` list accumulating all chunks from all files in `index_path` (pipeline.py:141) before `build_dependency_records` runs at the end (pipeline.py:197). Do NOT assert causality until Phase 0 confirms it via RSS measurement before/after `graph_chunks.extend()`. The fix (stream `build_dependency_records` per file) belongs to Spec C. Note: per-file streaming would lose cross-file `called_by` edges that `build_dependency_records` aggregates when receiving all chunks together; that trade-off must be evaluated in Spec C. |
| Reconcile is not gated on hash-skip (D6) | With stable ids (D1), unchanged files keep valid points even if their line numbers changed (no re-index needed on hash hit). A changed file (hash miss) runs `delete_by_file` + re-upsert, which clears deleted/renamed symbols. This is the actual mechanism resolving the orphan problem. The spec does not claim that per-file delete only triggers on hash miss as the solution for orphans - the solution is D1 + D6 together. |
| Acceleration order | Measure first (Phase 0) -> stable chunk-id + reconcile (D1/D6) -> chunk size cap (YAGNI + quality, conditional on `large_chunks_found > 0`) -> library native provider -> thread tuning -> only then consider multiprocessing pool if still needed |
| GPU | Conditional: only if P1 + P2 confirmed AND comparative measurement (P5) shows real gain for AXON's batch sizes. For batches of 5-30 chunks, GPU frequently loses due to PCIe overhead; cross-file batching may be needed to amortize that cost and is out of scope. |
| CoreML (Mac) | Same pattern: pass `providers=['CoreMLExecutionProvider','CPUExecutionProvider']` via kwarg only if fastembed exposes the kwarg (P2) and measurement shows benefit |
| Thread tuning | Use `SessionOptions.intra_op_num_threads` via `providers_options` (preferred). `OMP_NUM_THREADS` via `os.environ` in `_ensure_model` is a no-op because onnxruntime reads that variable at module import time, not at session instantiation. See detailed note in the implementation section. Only after P4 confirms underutilization. |
| Hand-rolled multiprocessing pool | YAGNI: do not build. fastembed/onnxruntime are already multi-threaded internally. Justifiable only if library-native does not hit targets after measurement. |
| Length-bucketed batching | Group chunks by token-count range before passing to `embed()` to reduce internal onnxruntime padding. Token budget per batch to avoid RSS spikes. Conditional on `large_chunks_found > 0` in Phase 0 (YAGNI for typical files of 5-30 chunks). |
| Token budget per batch | `MAX_BATCH_TOKENS = 8192` as default (adjustable via env var `AXON_MAX_BATCH_TOKENS`). If a single chunk exceeds the budget, it goes in its own batch (not discarded). |
| Chunk size cap | `_MAX_CHUNK_LINES = 80` estimated tokens per chunk (same value as Java, chunker.py:37). Conditional on `large_chunks_found > 0` in Phase 0. Positively impacts recall. Verify recall guard before and after. |
| Atomic delete+upsert flush per file | The delete (`delete_by_file`) and the upsert of new points from the same file are executed within the per-file iteration, without waiting for the `_BATCH_SIZE` flush. This eliminates the search window where a file has no points. See implementation section for detail. |
| Persistent `_FILE_HASH_CACHE` | Out of scope for this spec (covered by Spec C). |

---

## Components and changes

### 1. `axon/embedder/pipeline.py` - stable chunk-id (D1)

The current `_chunk_id` (pipeline.py:206-211) uses `start_line` in the key, which makes the id
unstable when lines above the symbol are edited.

New implementation:
```python
def _chunk_id(path: Path, chunk: Chunk, occurrence_index: int) -> str:
    """Stable ID: uuid5 of file_path::symbol::occurrence_index.
    occurrence_index is the 0-based index of the symbol within the file,
    disambiguating overloads and sub-chunks (foo[0], foo[1]).
    start_line does NOT enter the key - editing lines above does not change the id."""
    import uuid
    key = f"{path}::{chunk.symbol}::{occurrence_index}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))
```

**All call sites of `_chunk_id` must be updated to the new signature with `occurrence_index`.** There are two call sites in pipeline.py: `index_path` (line 173) and `ingest_file` (line 109). Both must pass `occurrence_index` as the 0-based index within the file's chunk list. The file `scripts/index_once.py` does not call `_chunk_id` directly and requires no change for D1.

The caller in `index_path` (pipeline.py:173) passes `occurrence_index` calculated as the
`enumerate` index within the current file's chunk list:
```python
vector_chunks = [
    VectorChunk(
        id=_chunk_id(file_path, c, i),
        ...
    )
    for i, (c, vec) in enumerate(zip(chunks, vectors))
]
```

The same `enumerate` pattern must be applied to the call site in `ingest_file` (pipeline.py:109),
passing `occurrence_index=i` as the third argument to `_chunk_id`.

Affected file: `C:/Users/samde/dev/axon/src/axon/embedder/pipeline.py` (function `_chunk_id`, call site in `index_path` line 173, call site in `ingest_file` line 109)

### 2. `axon/store/vector_store.py` - reuse delete_by_file (D4)

The method `delete_by_file(ctx, file_path)` already exists in `vector_store.py:163`.
Do NOT create `delete_file_points`, `delete_by_file_path`, or `_collections()`.

To delete points from a file across all contexts, use `COLLECTIONS` (vector_store.py:24):
```python
# In pipeline.py, inside the file loop, before the upsert
from axon.store.vector_store import COLLECTIONS
for ctx in COLLECTIONS:
    await store.delete_by_file(ctx, str(file_path))
```

The atomic flush is done WITHIN the per-file iteration - do not wait for `_BATCH_SIZE`:
```python
# pipeline.py - per-file loop (hash miss confirmed)
# 1. Write pending sentinel in file_index (D2)
# 2. Atomic delete across all contexts
for ctx in COLLECTIONS:
    await store.delete_by_file(ctx, str(file_path))
# 3. Immediate upsert of the new points (no defer to _BATCH_SIZE)
await store.upsert_batch(vector_chunks)
# 4. Mark status='done' in file_index (D2)
```

The existing `_BATCH_SIZE=400` continues controlling the flush of files that do not go
through reconcile (new files on the first index), but re-indexed files do an
atomic per-file flush.

Affected file: `C:/Users/samde/dev/axon/src/axon/store/vector_store.py` (no method changes)
Affected file: `C:/Users/samde/dev/axon/src/axon/embedder/pipeline.py`

### 3. `axon/embedder/pipeline.py` - crash-safety pending sentinel (D2)

The SQLite `file_index` table (Spec C) receives column `status TEXT NOT NULL DEFAULT 'done'`.

Re-indexing flow (hash miss):
```
(a) INSERT OR REPLACE INTO file_index(file_path, sha1, status) VALUES (?, ?, 'pending')
(b) DELETE Qdrant points (via delete_by_file loop over COLLECTIONS)
(c) UPSERT new Qdrant points
(d) UPDATE file_index SET status='done' WHERE file_path=?
```

At the startup of any run:
```python
# Treat 'pending' rows as dirty - re-index even when the hash matches
pending = db.execute("SELECT file_path FROM file_index WHERE status='pending'").fetchall()
for row in pending:
    _FILE_HASH_CACHE.pop(row["file_path"], None)  # forces re-index
```

One-shot migration of the 9 already-indexed repos uses BLUE/GREEN:
- Create new Qdrant collection with suffix `_v2` (or use Qdrant alias)
- Index everything with D1 ids in the new collection
- Run recall gate (Top-1 >= 0.90, Top-3 >= 0.95) against the new collection
- If passed: `client.update_collection_aliases(...)` to promote `_v2` as active collection
- If failed: keep old collection; investigate regression before promoting

Affected file: `C:/Users/samde/dev/axon/src/axon/embedder/pipeline.py`

### 4. `axon/embedder/pipeline.py` - walk scope restricted to tracked-only (D3)

Replace `rglob` in `iter_supported_files` (pipeline.py:70) with `git ls-files --cached`.
Filter via `git check-ignore` to exclude gitignored files even if committed.

```python
import subprocess

def _git_ls_files(target: Path) -> list[Path] | None:
    """Returns the list of tracked files via git ls-files --cached.
    Does not include untracked files (--others excluded for safety).
    Returns None if not a git repo."""
    try:
        result = subprocess.run(
            ["git", "-C", str(target), "ls-files", "--cached"],
            capture_output=True, text=True, check=True
        )
        candidates = [target / line for line in result.stdout.splitlines() if line]
        # Filter gitignored (files that were committed and later ignored)
        return [p for p in candidates if not _is_gitignored(target, p)]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

def _is_gitignored(repo_root: Path, path: Path) -> bool:
    """True if git check-ignore classifies the path as ignored."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "check-ignore", "--quiet", str(path)],
            capture_output=True
        )
        return result.returncode == 0
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

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

    git_files = _git_ls_files(target)
    candidates = git_files if git_files is not None else _rglob_fallback(target)
    for path in candidates:
        if any(part in EXCLUDED_DIR_NAMES for part in path.parts):
            continue
        language = _language_for_suffix(path.suffix)
        if path.is_file() and language and (languages is None or language in languages):
            yield path

def _rglob_fallback(target: Path) -> Iterable[Path]:
    """Fallback para rglob quando fora de repo git. Sem mudanca de logica."""
    return target.rglob("*")
```

Affected file: `C:/Users/samde/dev/axon/src/axon/embedder/pipeline.py`

### 5. `axon/embedder/engine.py` - auto-detected provider

**Dependency on assumptions**: P1 (GPU available), P2 (fastembed accepts providers), P4 (threads).

New function `_detect_providers() -> list[str]`:
```python
# engine.py - new function, called ONCE in _ensure_model()
def _detect_providers() -> list[str]:
    import onnxruntime as ort
    available = set(ort.get_available_providers())
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        if "CoreMLExecutionProvider" in available:
            return ["CoreMLExecutionProvider", "CPUExecutionProvider"]
    elif "CUDAExecutionProvider" in available:
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]
```

`_ensure_model()` updated (engine.py:56-62):
```python
def _ensure_model(self) -> TextEmbedding:
    if self._model is None:
        providers = _detect_providers()
        self._model = TextEmbedding(
            model_name=self.model_name,
            cache_dir=str(self.cache_dir),
            providers=providers,          # new argument - conditional on P2
        )
    return self._model
```

**Safe fallback**: if `fastembed 0.8.0` does not accept `providers` (P2 false), the kwarg is
omitted and behavior is identical to the current. Verifying P2 determines whether the kwarg is
included or a version upgrade is required first.

**Thread tuning - important note**: `os.environ["OMP_NUM_THREADS"] = ...` in `_ensure_model`
is a NO-OP because onnxruntime reads `OMP_NUM_THREADS` at module import time, not
at session instantiation. Setting the variable after the import has no effect. The correct approach
is to use `SessionOptions.intra_op_num_threads` via fastembed's `providers_options`,
if the kwarg is available (P2). Alternative: set `OMP_NUM_THREADS` before importing
onnxruntime (e.g. via entry script, not inside engine.py). Before
implementing any thread tuning, measure CPU utilization with `psutil.cpu_percent(percpu=True)`
during an index to confirm underutilization (P4); if all cores are already busy,
tuning adds no gain.

Affected file: `C:/Users/samde/dev/axon/src/axon/embedder/engine.py`

### 6. `axon/embedder/pipeline.py` - token-bounded batching (conditional)

**Conditional on `large_chunks_found > 0` in Phase 0. If all chunks are < 512 tokens
(typical for files of 5-30 functions), this section is YAGNI and will not be implemented.**

Token estimation constant:
```python
# pipeline.py - after line 29
_MAX_BATCH_TOKENS: int = int(os.environ.get("AXON_MAX_BATCH_TOKENS", "8192"))
_MAX_CHUNK_TOKENS: int = int(os.environ.get("AXON_MAX_CHUNK_TOKENS", "512"))
_TOKENS_PER_CHAR: float = 0.35  # DELIBERATE OVERESTIMATE for batch-memory safety cap.
# Why 0.35 and not 0.25 (len//4)?
# vector_store.py:153 uses len(content)//4 (= 0.25 tokens/char) for the OUTPUT budget,
# where underestimating is acceptable (output stays within budget). Here the goal is the opposite:
# ensure the batch does NOT exceed the INPUT/memory budget of onnxruntime. Underestimating tokens
# would mean assembling batches larger than allowed, risking an RSS spike. That is why we use
# 0.35 (conservative overestimate: ~2.86 chars/token vs the ~4 chars/token BPE average).
# The exact value must be calibrated empirically in Phase 0 with AXON's real corpus.
```

New function `_estimate_tokens(text: str) -> int`:
```python
def _estimate_tokens(text: str) -> int:
    return max(1, int(len(text) * _TOKENS_PER_CHAR))
```

Length-bucketed batching function:
```python
def _make_token_bounded_batches(
    chunks: list[Chunk],
) -> list[list[Chunk]]:
    """Groups chunks into batches that do not exceed _MAX_BATCH_TOKENS.
    Never discards a chunk; if a single chunk exceeds the cap, it goes
    in its own batch."""
    batches: list[list[Chunk]] = []
    current: list[Chunk] = []
    current_tokens = 0
    for chunk in chunks:
        tokens = _estimate_tokens(chunk.content)
        if current and current_tokens + tokens > _MAX_BATCH_TOKENS:
            batches.append(current)
            current = []
            current_tokens = 0
        current.append(chunk)
        current_tokens += tokens
    if current:
        batches.append(current)
    return batches
```

`index_path` updated: instead of `engine.embed([c.content for c in chunks])` per file
(pipeline.py:170), split into sub-batches via `_make_token_bounded_batches(chunks)` and
concatenate results. This change is only introduced if Phase 0 confirms `large_chunks_found > 0`.

### 7. Chunk size cap in chunker (quality + memory) - conditional

**Conditional on `large_chunks_found > 0` in Phase 0 (same gate as section 6).**

If `large_chunks_found > 0`, add cap in `chunk_source` for Python and TypeScript
(Java already has `_MAX_CHUNK_LINES = 80` in chunker.py:37).

The existing `_split_large_node` function (chunker.py:217-241) receives a tree-sitter `Node`
and CANNOT be reused directly by the Python/TypeScript chunkers that already build
`Chunk` before any split. The correct implementation is a separate function that operates
on an already-built `Chunk`:

```python
# chunker.py - new function, independent of tree-sitter
def _split_large_chunk_by_lines(chunk: Chunk, max_lines: int) -> list[Chunk]:
    """Split a Chunk that exceeds max_lines into line-based sub-chunks.
    Works on any Chunk (Python, TypeScript, Java).
    Each sub-chunk's symbol gets a [i] suffix for disambiguation
    (compatible with the occurrence_index from D1)."""
    lines = chunk.content.splitlines()
    if len(lines) <= max_lines:
        return [chunk]
    result: list[Chunk] = []
    for i in range(0, len(lines), max_lines):
        part_lines = lines[i : i + max_lines]
        result.append(
            Chunk(
                symbol=f"{chunk.symbol}[{i // max_lines}]",
                chunk_type=chunk.chunk_type,
                start_line=chunk.start_line + i,
                end_line=chunk.start_line + i + len(part_lines) - 1,
                content="\n".join(part_lines),
                file_path=chunk.file_path,
                language=chunk.language,
            )
        )
    return result
```

This function is called in `_chunk_python` and `_chunk_typescript` after building each chunk,
replacing large chunks with the list of sub-chunks.

**This change requires a full recall guard run before and after** (see section below).

Affected file: `C:/Users/samde/dev/axon/src/axon/embedder/chunker.py`

---

## Data flow (after)

```
axon index <repo>
  |
  +-- startup: mark 'pending' rows in file_index as dirty (D2)
  |
  +-- iter_supported_files(repo)
  |     git ls-files --cached (if git repo) - without --others (D3)
  |     filter gitignored via git check-ignore (D3)
  |     OR rglob (non-git fallback)
  |     excludes EXCLUDED_DIR_NAMES
  |
  +-- for each file (hash-cache check):
  |   hash hit + status='done': skip (stable id via D1 = no orphans)
  |   hash miss OR status='pending':
  |     (a) file_index: status='pending' + new sha (D2)
  |     chunks = chunk_source(source, language, path)
  |       [if large_chunks_found>0]: _split_large_chunk_by_lines per large chunk
  |     [if large_chunks_found>0]: _make_token_bounded_batches(chunks)
  |     engine.embed(texts)
  |       TextEmbedding(providers=[CUDA|CoreML|CPU])  <- auto-detected (P1+P2)
  |       SessionOptions.intra_op_num_threads         <- if P4 confirmed
  |     vector_chunks with D1 ids (uuid5 file::symbol::occurrence_index)
  |     atomic DELETE: delete_by_file(ctx, path) for each ctx in COLLECTIONS (D4)
  |     immediate UPSERT: store.upsert_batch(vector_chunks)
  |     (d) file_index: status='done' (D2)
  |     graph_chunks.extend(chunks)
  |
  +-- final flush -> Qdrant (new files not reconciled)
  +-- build_dependency_records(graph_chunks) -> Redis
```

**Note**: the cause of the RSS spike in the `graph_chunks` list is hypothesis P3, to be confirmed
in Phase 0. The fix (per-file streaming) belongs to Spec C (D5).

---

## Quality guard (Recall Guard)

The recall guard is a fixed set of 20 pairs `(query, expected_file, expected_symbol,
min_score)` stored in `tests/recall/golden_set.json`. This file is built ONCE,
with human verification, BEFORE any chunker or embedder change. The golden
set MUST NOT be generated automatically from the current index, as that would encode
pre-existing bugs as ground truth. Each pair must be manually verified: does the query
return the correct expected_file/symbol in the current index? If not, it is a bug to fix, not
a golden set item.

Golden set distribution:
- 8 Python function queries
- 5 Java method queries
- 4 TypeScript function queries
- 3 cross-file / architectural queries

For each query, the harness verifies:
- `check name='top_1_file_match'`: `hits[0].payload['file_path'] == expected_file`
- `check name='top_3_file_match'`: `expected_file` in `{hits[0..2].payload['file_path']}`
- `check name='min_score'`: `hits[0].score >= min_score` (floor: 0.70)
- `check name='symbol_match'`: `hits[0].payload['symbol'] == expected_symbol`

Implementation reuses `BenchmarkCheck` and `BenchmarkResult` from
`src/axon/benchmark/contracts.py` (existing shape, no modification).

The recall harness (`RecallBenchmarkFixture`) uses REAL embedding (not mocked) and real Qdrant
via `testcontainers[qdrant]` (already in `pyproject.toml [dev]`). The reference corpus
is `src/axon/embedder/` + `src/axon/store/` (small enough to index in < 60 s).

**Regression gate** (`tests/recall/test_recall_guard.py`):
```python
def test_no_regression():
    baseline = load_json("tests/recall/baseline.json")
    current  = run_recall_harness()
    report   = compare_benchmark_runs(current, baseline)
    assert len(report.regressions) == 0, report.regressions
    assert current.score >= 0.90
```

This test blocks any PR that regresses recall. The baseline is updated explicitly
(separate commit) only when a change demonstrably improves quality.

**Note on model and embedding in the harness**: if hypothesis P3 is confirmed (`graph_chunks`
list causes the RSS spike), run embedding in an isolated subprocess via
`subprocess.run(['python', '-m', 'axon.bench.embed_worker', ...])` to keep the benchmark
process lean. The embed_worker receives the corpus and returns JSON with vectors via stdout.

---

## Units (isolation)

| Unit | File | Responsibility | Injectable dependencies |
|---|---|---|---|
| `_detect_providers()` | `engine.py` | Detects available providers via onnxruntime | none (calls `ort.get_available_providers()`) |
| `EmbedderEngine._ensure_model()` | `engine.py:56-62` | Instantiates `TextEmbedding` with detected providers | `_detect_providers()` mockable |
| `_chunk_id(path, chunk, occurrence_index)` | `pipeline.py:206-211` | Stable ID via uuid5 without start_line (D1) | none |
| `_estimate_tokens()` | `pipeline.py` | Token estimate per chunk (intentional overestimate) | none |
| `_make_token_bounded_batches()` | `pipeline.py` | Groups chunks without exceeding token budget (conditional) | none |
| `_split_large_chunk_by_lines()` | `chunker.py` | Splits large Chunk into sub-chunks by lines (conditional) | none |
| `_git_ls_files()` | `pipeline.py` | Lists tracked files via `git ls-files --cached` (D3) | subprocess mockable |
| `_is_gitignored()` | `pipeline.py` | Checks `git check-ignore` to exclude gitignored files (D3) | subprocess mockable |
| `iter_supported_files()` | `pipeline.py:59-75` | Walker with rglob fallback | `_git_ls_files`, `_is_gitignored` mockable |
| `VectorStore.delete_by_file(ctx, file_path)` | `vector_store.py:163` | Deletes points by file_path in one context (existing - D4) | Qdrant client mockable |
| `RecallBenchmarkFixture` | `axon/benchmark/recall.py` (new) | Runs queries on the golden set | `VectorStore`, `EmbedderEngine` injectable |

---

## End-to-end verification

1. **Provider detection**: after wheel swap (if P1 confirmed), `python -c
   "from axon.embedder.engine import _detect_providers; print(_detect_providers())"` must
   print `['CUDAExecutionProvider', 'CPUExecutionProvider']` on desktop.

2. **Throughput with correct provider**: synthetic corpus of 500 functions; throughput >=
   Phase 0 baseline + measurable improvement on desktop. If GPU does not help (P5), target is
   met via thread tuning or batching. Final numeric target defined in Phase 0.

3. **RSS below cap**: `axon index <vault_root>` with psutil sampling; peak RSS <= 2 GB
   on desktop.

4. **Stable chunk-id (D1)**: index file, edit 3 lines above the first symbol
   (without changing the symbol), re-index; `vector_store.scroll(filter=file_path)` must return
   exactly the same set of ids as before the edit.

5. **Reconcile works (D6)**: index file, remove a symbol, re-index;
   `vector_store.scroll(filter=file_path)` must return a reduced chunk count (removed symbol
   is no longer there), with no orphan points.

6. **Gitignored files excluded (D3)**: repo with `.env` committed and then added to
   .gitignore; after indexing, scroll Qdrant with filter `file_path contains ".env"` must
   return zero results.

7. **Recall guard passes**: `pytest tests/recall/test_recall_guard.py` with 0 regressions;
   `current.score >= 0.90`.

8. **Full index wall time**: median of 3 runs with cold hash-cache; <= 5 min on desktop,
   <= 8 min on M1 Pro.

9. **Fallback without GPU**: if `CUDAExecutionProvider` unavailable, `_detect_providers()`
   returns `['CPUExecutionProvider']` and `engine.embed()` works normally (no error).

10. **Crash-safety (D2)**: simulate crash after `status='pending'` + Qdrant delete but before
    upsert; restart; the file must be automatically re-indexed (pending row
    detected at startup).

---

## Tests

### Unit tests (no model load, no Qdrant)

- `test_chunk_id_stable_ignores_line_shift`: same `_chunk_id` for chunk with `start_line=10`
  and `start_line=13`; only symbol and occurrence_index enter the key (D1).
- `test_chunk_id_disambiguates_overloads`: two chunks with same symbol, occurrence_index
  0 and 1; different ids.
- `test_detect_providers_cuda`: mocks `ort.get_available_providers()` returning
  `['CUDAExecutionProvider', 'CPUExecutionProvider']`; assert return includes CUDA first.
- `test_detect_providers_cpu_fallback`: mocks available = `['CPUExecutionProvider']`;
  assert return = `['CPUExecutionProvider']`.
- `test_detect_providers_coreml_mac`: mocks `platform.system()='Darwin'`,
  `platform.machine()='arm64'`, available includes `CoreMLExecutionProvider`; assert
  return = `['CoreMLExecutionProvider', 'CPUExecutionProvider']`.
- `test_make_token_bounded_batches_teto` (conditional on large_chunks_found): 10 chunks of
  1000 tokens each with budget 8192; verifies that resulting batches do not exceed budget and
  sum of chunks = 10.
- `test_make_token_bounded_batches_chunk_gigante` (conditional): 1 chunk with 20,000 tokens;
  goes in its own batch (not discarded).
- `test_split_large_chunk_by_lines` (conditional): chunk of 200 lines with max_lines=80;
  result = 3 sub-chunks with symbols foo[0], foo[1], foo[2].
- `test_estimate_tokens_overestimates`: text of 100 chars; `_estimate_tokens` returns >=
  35 (uses 0.35, not 0.25).
- `test_git_ls_files_excludes_untracked`: subprocess mocked returning only tracked files;
  untracked files do not appear.
- `test_git_ls_files_excludes_gitignored`: `_is_gitignored` mocked returning True for
  `.env`; `.env` does not appear in the result.
- `test_iter_supported_files_fallback_rglob`: `CalledProcessError` raised by
  `_git_ls_files`; `iter_supported_files` uses rglob.
- `test_delete_by_file_loops_collections`: `store.delete_by_file` mocked; verifies that
  it is called once for each entry in `COLLECTIONS`.

### Integration tests (Qdrant via testcontainers, no model load - embedder mocked)

- `test_reconcile_sem_orfaos_chunk_id_estavel`: index file (3 mocked chunks),
  edit start_line simulating lines-above edit (symbol unchanged), re-index;
  scroll must return 3 points with identical ids (D1).
- `test_reconcile_removed_symbol`: index file (3 chunks), re-index with 2 chunks
  (1 symbol removed); scroll must return 2 points, not 3 (D6).
- `test_gitignored_excluido_do_indice`: simulated git repo with `.env` tracked and then
  gitignored; after `index_path`, Qdrant scroll must return no points with
  `file_path` containing `.env` (D3).
- `test_pending_sentinel_reindex_apos_crash`: insert row `status='pending'` in file_index;
  confirm that startup marks the file as dirty and re-indexes it (D2).
- `test_flush_atomico_delete_upsert`: verify that delete and upsert occur within the
  file's iteration (not deferred to the _BATCH_SIZE flush); after the file loop,
  scroll returns new points.
- `test_idempotencia_provider_fallback`: `_ensure_model` is called twice; `TextEmbedding`
  is instantiated only once (correct lazy init).

### Recall guard (real embedding + Qdrant container)

- `tests/recall/test_recall_guard.py::test_no_regression` - see "Quality guard" section.
- `tests/recall/test_recall_guard.py::test_top1_gte_090` - Top-1 score >= 0.90.
- `tests/recall/test_recall_guard.py::test_top3_gte_095` - Top-3 score >= 0.95.

Target coverage: 80%+ on new and modified units.

---

## Out of scope

- Persistent hash cache between processes (SQLite, file_index, reconcile) - covered by Spec C.
- Redis pipeline for sequential `upsert_deps` - covered by Spec C.
- Double AST re-parse in `graph_extractor.py` (parse-once / re-parse removal) - covered by Spec A.
- Streaming `build_dependency_records` per file (fix for hypothetical RSS spike) - Spec C (D5).
- Changes to the set of supported languages (`_LANGUAGE_MAP`).
- Hand-rolled multiprocessing pool for embedding - YAGNI until measurement proves necessity.
- Cross-file batching to amortize GPU PCIe overhead - out of scope; must be
  confirmed via P5 probe before even considering.
- Model upgrade (bge-large, bge-m3, etc.) - would change dimensions and invalidate existing collections.
- Markdown/text chunking with tree-sitter - out of this performance overhaul.
- Distributed locking between concurrent hook and manual index - identified as risk but
  handled in Spec C (lockfile, per-file reconcile) or as future work with asyncio.Lock.
- Ongoing BLUE/GREEN migration after the one-shot of the 9 repos - normal runs use
  atomic delete_by_file per file, not blue/green (D2).
