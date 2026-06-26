# Design: `axon init` performs complete onboarding (symbols + embeddings)

Date: 2026-06-19
Status: approved (awaiting review of the written spec)
Scope: unify code indexing in `axon init`. No auto-refresh via hook (out of scope).

## Context

Today onboarding a repo requires **two commands**:

- `axon init <repo>` -> installs git hooks + `index_repo` (symbols in **SQLite**, `.py`/`.java` only).
- `python -m axon.cli.pb index <repo> --ctx <ctx>` -> `index_path` (embeddings in **Qdrant**,
  `.py`/`.java`/`.ts`/`.md`/`.txt`) + dep records in Redis.

The two populate **complementary**, not redundant, stores: `search_code` does the semantic
match in **Qdrant** and enriches the hit with the symbol subgraph from **SQLite**. Without the
embedding step, `search_code` returns nothing. Having `pb index` as a separate manual step
"stopped making sense" now that the focus is on evolving AXON itself.

Facts verified in the code:
- `axon.code.indexer.index_repo(repo, *, store)` -> SQLite symbol nodes (`.py`/`.java`).
- `axon.embedder.pipeline.index_path(target, *, engine, store, vault_root, forced_ctx, graph_store, languages)`
  -> embeddings in Qdrant by ctx + dep records in Redis. Already **incremental** (hash-cache per
  file) and excludes `node_modules`/`.venv`/`dist`/etc.
- `index_path` skips files with ctx `work` **unless** `forced_ctx == "work"`.
- Embedder is **local** (fastembed `BAAI/bge-base-en-v1.5`, 768-dim, no API key).
- The setup of `EmbedderEngine` + `VectorStore` + `GraphStore` is **duplicated** across ~5 commands
  in `pb.py` (`index`, `index-dev`, etc.).

## Decisions

| Topic | Decision |
|---|---|
| `init` scope | `axon init` = hooks + SQLite symbols + **Qdrant embeddings**, in one call. Refresh is manual (re-run `axon init`). |
| Default ctx | `--ctx knowledge` (default). Code = knowledge base; it is in the default search set. |
| `work` ctx | **Only** when `--ctx work` is passed explicitly. The default never writes to `work`. |
| Approach | Extract a single helper `embed_repo(...)` consumed by `init` **and** `pb`; removes duplication from `pb.py`. |
| Degradation | Qdrant down -> `init` does not fail: installs hooks + symbols and warns that embeddings were skipped. Redis down -> dep records skipped. |
| Migration | Re-index the 9 onboarded repos from `personal` -> `knowledge` and clean up the `personal` collection. |
| Out of scope | Auto-refresh of the index via git hook on commit. `pb index`/`index-dev` continue to exist (vault/manifest use case). |

## Components and changes

### 1. New isolated helper - `axon/code/embedder.py`
```
async def embed_repo(
    repo_path: Path | str,
    *,
    ctx: str = "knowledge",
    engine: EmbedderEngine | None = None,
    store: VectorStore | None = None,
    graph_store: GraphStore | None = None,
) -> tuple[int, int]:  # (indexed_files, total_chunks)
```
- Single responsibility: embed a repo into a ctx. Builds engine/store/graph_store if not
  injected (default: runtime config), calls `ensure_collections()` and
  `index_path(repo_path, engine=..., store=..., vault_root=..., forced_ctx=ctx, graph_store=...)`.
- `forced_ctx=ctx` routes every file to the chosen ctx (overrides `infer_ctx_from_path`).
- Closes store/graph_store at the end (`finally`).
- Testable in isolation: inject mocks, count chunks by ctx, validate idempotence (hash-cache).

### 2. `axon init` (in `axon/__main__.py`)
- New option: `--ctx` (default `"knowledge"`).
- Flow: `install_hooks` -> `index_repo` (symbols) -> `embed_repo(repo, ctx=ctx)` (embeddings).
- Aggregated output:
  ```
  hooks installed: post-commit, pre-push, post-merge, post-checkout
  indexed N symbols from <repo>
  embedded M chunks into ctx=knowledge
  ```
- Degradation: the embedding step is wrapped so that a Qdrant/Redis failure becomes a warning
  (`embeddings skipped (<reason>)`), without aborting the `init` or changing the exit code for
  steps that succeeded.

### 3. `pb.py` refactor (DRY, contained scope)
- `pb index` and `pb index-dev` will call `embed_repo`/`index_path` via the same helper path,
  eliminating the duplicated engine/store/graph_store setup. External behavior unchanged (same
  flags, same output).

### 4. One-shot migration (part of the delivery, not permanent code)
- Re-index the 9 onboarded repos with `ctx=knowledge`.
- Clean the `personal` collection (today contains only code indexed during the integration).
- Verify: `search_code` (without ctx) returns hits from `knowledge`.

## Data flow (after)

```
axon init <repo> - install_hooks -> .git/hooks/*
                 - index_repo    -> SQLite symbol graph (.py/.java)
                 - embed_repo    -> Qdrant (ctx=knowledge) + Redis dep records (.py/.java/.ts/.md/.txt)
search_code <q>  - embed(q) -> Qdrant(knowledge,...) -> enrich via SQLite subgraph
```

## Units (isolation)

- **embed_repo** (`axon/code/embedder.py`) - embeds a repo into a ctx; depends on
  engine/store/graph_store; testable with mocks.
- **init** (`axon/__main__.py`) - orchestrates hooks + symbols + embeddings; depends on
  `install_hooks`, `index_repo`, `embed_repo`.
- **pb index/index-dev** - delegate to the same path; no interface change.

## Verification (end-to-end)

1. `axon init <py-repo>` on a clean repo -> prints the 3 steps; `embedded M chunks` with M>0.
2. Qdrant stopped -> `axon init` still installs hooks + symbols and warns `embeddings skipped`; exit 0.
3. `search_code "<known symbol>"` returns the snippet from the newly `init`-ed repo (default ctx).
4. Re-run `axon init` on the same repo with no changes -> `embedded 0 chunks` (hash-cache).
5. `--ctx work` -> embeddings go to `work` collection and **do not** appear in `search_code` without ctx.
6. Migration: `personal` collection empty/removed; the 9 repos searchable via `knowledge`.

## Tests

- Unit `embed_repo`: chunk count, correct ctx, idempotence (hash-cache), store/graph closed.
- Unit/integration `init`: all 3 steps run; "qdrant down does not break" path (mock that raises).
- Regression `pb index`: output/flags unchanged after the refactor.
- Target coverage: 80%+ on new/changed units.

## Out of scope
- Auto-refresh of the index via git hook on commit (automatic incremental re-index).
- Remove or rename `pb index`/`index-dev`.
- Change the set of languages supported by the embedder.

## Performance validation (PENDING - dedicated pass)

Preliminary measurement (1 sample, machine under load; treat as order of magnitude,
not a final number):

| Scenario | Throughput |
|---|---|
| fastembed model load (1x/process) | ~0.6s |
| Short chunks (small functions) | ~240 chunks/s |
| Long chunks (~300 tokens) | ~3 chunks/s |

Throughput is dominated by chunk size. Real onboarding of the 9 repos (~4555
chunks) took a few minutes because it was a mix. **Measurement needs to be redone
in a dedicated perf pass, with an idle machine**, before closing the scope below.

Risks/findings raised by this validation that the spec still needs to address:

1. **Memory.** The long-chunk path overflowed ~14 GB in a benchmark process.
   `axon init` must not be able to take down the machine -> likely need for a
   memory ceiling / chunk size cap / smaller batch. **To be confirmed in the perf pass.**
2. **No cross-run incrementality.** `_FILE_HASH_CACHE` is in-memory, per-process.
   So today **every `axon init` re-embeds everything** (cost = minutes on a large repo).
   Cheap refresh would require persistent state (hashes in SQLite). Decide if this enters scope.
3. **Threading.** 16 cores, `OMP_NUM_THREADS` unset; the ~3/s on long text suggests
   possible core underutilization. Check onnxruntime/fastembed config (without heavy
   benchmarking) - may change the cost calculation.

**Open scope decision** (depends on the perf pass): minimum (wire only) vs
medium (wire + memory-safety + progress) vs complete (+ persistent incremental).
Do not proceed to the implementation plan until this validation closes the scope.
