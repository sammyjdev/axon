# GLYPH integration — follow-ups (dec-116)

Continuation notes for finishing the GLYPH graph-retrieval delegation and the
cleanups it surfaced. Self-contained so it can be resumed on another machine.

- **Last updated:** 2026-06-12
- **Related decision:** `docs/decisions/dec-116-glyph-graph-delegation.md`
- **GLYPH repo:** `github.com/sammyjdev/glyph-kg` (local: `~/dev/glyph-kg`)

## ✅ Re-pinned and revalidated against finalized GLYPH

GLYPH `main` reads as a **completed P0–P7** state (see the snapshot below; owner
confirmed only docs remain in flight). Re-pin done, seam re-validated, and the
graph tests pass end-to-end against the new SHA. Item #1 is **closed**.

1. ✅ **Re-pinned** AXON to GLYPH `main`
   `5366308afea2a0cb0f3a1353e1a78c2c167ea8d9` in `pyproject.toml` (still a SHA —
   GLYPH has no tags yet, see #5). Verified the SHA is on `origin/main`, fully
   pushed (0 ahead / 0 behind), so the `git+https` ref resolves — confirmed by a
   clean `pip install -e ".[dev]"` that built `glyph-kg 0.0.0` from this pin.
2. ✅ **Seam re-checked** against this SHA (live, post-install):
   `glyph.integration.GraphContextSource` is still `__init__(store, embedder,
   nodes, *, hops=2, anchors=3)` + `.retrieve(query, token_budget=1000) ->
   ContextPack` (method is `.retrieve()`, **not** `.context()`).
   `src/axon/context/graph_source.py` still matches.
3. ✅ **AXON graph tests pass** against the finalized GLYPH — **11 passed** (2026-06-12,
   desktop R7 5800X3D / RTX 4070 Ti):
   ```bash
   PYTHONPATH=src python3 -m pytest tests/context/test_graph_source.py \
     tests/mcp/test_graph_context_tool.py tests/store/test_graph_listing.py -q
   ```

## ✅ Done (merged in PR #19 → master)

- **dec-116 delegation** — `src/axon/context/graph_source.py` (`GraphContextSource`,
  `GlyphEmbedderAdapter`, `map_node_type`/`map_edge_type`), `all_nodes()`/
  `all_edges()` on `SessionStore`, MCP tool `get_graph_context`.
- **Decision-ref alignment** — stale "ADR-102/103" → `dec-116`/`dec-101` in
  docstrings + `pyproject.toml` (the explanatory note in dec-116 is kept on purpose).
- **Delegate to the official seam** — `graph_source.py` builds `NetworkXStore`
  by hand and delegates retrieval to `glyph.integration.GraphContextSource`
  (ADR-G6), instead of wiring `GraphRetriever` directly.

## GLYPH state (snapshot 2026-06-12)

- `main` has advanced to `5366308afea2a0cb0f3a1353e1a78c2c167ea8d9`. AXON's pin
  (`01c63f06a27ce02dc02d98df35c5aa962500437f`) is now a strict ancestor — a clean
  fast-forward, no divergence. Newly merged since the pin: **P7** (global
  community axis + `CommunityRetriever`, ADR-G7) and **P4.4** (code-domain
  benchmark — *vector beat graph* in the code domain — plus cross-family judge
  validation). HEAD is "Run the global community-axis benchmark (P7 end-to-end)";
  `1f111af` reads "docs: finalize README + plan to the completed P0-P7 state" →
  GLYPH now presents as a **completed P0–P7** state.
- CI present (`ci.yml`, `benchmark.yml`). Suite count not re-verified at this HEAD
  (the pre-finalization snapshot reported 151 passed; rerun before re-pinning).
- `version = "0.0.0"`, **0 tags** → AXON can still only pin by SHA today (see #5).
- The packaging break (`allow-direct-references` for the `eval`/`gnomon-eval`
  direct ref) is internal to GLYPH and already fixed on `main`; AXON only uses the
  `[retrieval]` extra and is unaffected.
- **Seam re-validated at this HEAD:** `glyph.integration.GraphContextSource` is
  still `__init__(store, embedder, nodes, *, hops=2, anchors=3)` +
  `.retrieve(query, token_budget=1000) -> ContextPack` (and `from_graph_file`).
  Item #2's check passes — `src/axon/context/graph_source.py` still matches.

## Remaining work items

Three small, independent items + one larger follow-up. Order: #6 (highest value,
isolated) → #3 (cheap hygiene) → #5 (after GLYPH tag) → #4 (own PR, TDD).

### #3 — ✅ Make the GLYPH import lazy (graceful fallback)

> **Done (branch `chore/glyph-dec116-followups`).** Top-level
> `from axon.context.graph_source import ...` removed from `server.py`; the import
> is now lazy inside `_get_graph_embedder` and `get_graph_context` (the latter
> returns an install hint on `ModuleNotFoundError`). Covered by
> `tests/mcp/test_graph_context_fallback.py`. Original analysis below.


- **Problem:** `graph_source.py` imports `glyph.*` at module top
  (`from glyph.integration import ...`, `glyph.model.*`, `glyph.store.networkx_store`),
  and `src/axon/mcp/server.py` imports `graph_source` at top. So if GLYPH is not
  installed, importing `axon.mcp.server` fails and **every MCP tool dies**, not
  just the graph one. GLYPH is a hard dependency, so this is defense-in-depth.
- **Fix (~20 min, own PR):** move the `graph_source` import into the function
  bodies (`_get_graph_embedder` at `server.py:69`, `get_graph_context` at
  `server.py:691`);
  wrap with `try/except ModuleNotFoundError` returning a clean message
  ("GLYPH not installed; `pip install glyph-kg[retrieval]`") instead of a stacktrace.
- **Test:** monkeypatch to simulate `ModuleNotFoundError` → tool degrades, the
  rest of the server still imports.

### #4 — Retire the legacy Redis `traverse` enrichment in the retrieval path

- **Problem:** the shared helper `_retrieve_context` (`server.py:~325-332`) still
  appends a "## Dependencias relacionadas (2-step)" block via Redis
  `GraphStore.traverse`. The merged GLYPH seam is now the canonical replacement.
- **Scope caveat (attribution fix):** this block lives in `_retrieve_context`, not
  in `search_code` directly — and `_retrieve_context` is called by **two** tools
  (`search_code` at `server.py:367` **and** the caller at `server.py:558`).
  Retiring it touches **both** call sites, not just `search_code`.
- **Scope note:** `GraphStore`/Redis is used widely (indexing `upsert_deps`, `pb`
  CLI, git hooks) — this is **not** a Redis removal, only swapping this one
  read-time enrichment block. Redis stays as the structural cache (D4/dec-101).
- **Mapping caveat:** legacy lists *related dependency node names*; the GLYPH seam
  returns *scored text segments*. Not 1:1 — decide what "related deps" becomes
  (e.g. `get_graph_neighbors` over SQLite, or `GraphContextSource`).
- **Effort:** medium, behavior change → **own PR with TDD**. Open as an issue.

### #5 — Pin GLYPH by tag instead of SHA

- **Blocked on GLYPH:** `version = 0.0.0`, 0 tags. Prerequisite (merge the feature
  branch to `main`) is **resolved** — `main` is merged + pushed and release-ready.
- **Steps once GLYPH is finalized:**
  1. In `~/dev/glyph-kg`: bump `version` (e.g. `0.1.0`), `git tag v0.1.0`, push tag.
     (Optional: publish to PyPI to drop the `git+https` direct ref entirely.)
  2. In AXON `pyproject.toml`: change the pin `@01c63f06…` → `@v0.1.0` (or
     `glyph-kg[retrieval]==0.1.0` if published).

### #6 — ✅ `.gitignore` swallows the architectural lexicon (latent bug)

> **Done (branch `chore/glyph-dec116-followups`).** Anchored `.gitignore`
> `data/` → `/data/` (root docker volume still ignored; `src/axon/data/` freed —
> verified no other `src/**/data/` was un-ignored) and committed the lexicon at
> `src/axon/data/architectural_lexicon.txt`. The original file was **never
> committed and is lost** (only on the M1); the committed list is a
> **reconstruction** from the gate's tests + intent — flagged in the file header
> for validation against the M1 original. `tests/adr/gates/test_density.py` now
> passes from a clean tree (8 passed). Original analysis below.


- **Problem:** `src/axon/adr/lexicon.py` loads
  `src/axon/data/architectural_lexicon.txt` (its docstring says this "ships" with
  the package). But `.gitignore:25` has an **unanchored** `data/` rule (meant for
  the docker runtime volume at repo root), which also matches `src/axon/data/`. So
  the file was **never tracked** — it exists only on the original machine. On any
  fresh clone / CI / worktree the dec-111 density gate breaks
  (`tests/adr/gates/test_density.py` → `FileNotFoundError`). This is the root cause
  of the density test failures seen in clean checkouts; it is **not** related to
  dec-116.
- **Fix (small, own PR):**
  1. Anchor the rule: `.gitignore` `data/` → `/data/` (ignores only the repo-root
     runtime volume, frees `src/axon/data/`).
  2. `git add src/axon/data/architectural_lexicon.txt` and commit (curated package
     data, belongs in the repo).
  3. Verify no other legitimate `src/**/data/` got un-ignored unintentionally.
- **Verify:** `PYTHONPATH=src python3 -m pytest tests/adr/gates/test_density.py -q`
  passes from a clean clone.

## Explicitly out of scope (decided against)

- **GLYPH P4 tree-sitter code extractor** — AXON has its own chunker (D5, an
  axon-owned release gate). Overlap is intentional; do **not** delegate extraction.
- **`GraphContextSource.from_graph_file(path, …)`** — AXON persists the graph in
  SQLite, not files; the current "build `NetworkXStore` in memory + pass to the
  seam" path is correct.
