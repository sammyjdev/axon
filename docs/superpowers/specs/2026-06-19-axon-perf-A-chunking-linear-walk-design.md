# Design: Perf Overhaul A - Universal chunk cap, single parse, and file-walk via git ls-files

Date: 2026-06-19
Status: draft (awaiting Phase 0 gate)
Scope: three orthogonal changes in the indexing path (chunker + pipeline + file-walk) that
form the **linear** pillar of the performance overhaul. Does not include parallelism or GPU.

This is Spec A of three (A = linear, B = cacheable, C = parallel). All three share the
same numeric success criteria and the same 20-query recall-guard.

---

## Design decisions applied in this spec (D1-D6)

The decisions below were consolidated after code review and supersede any conflicting text
that may have existed in previous drafts.

**D1 - Stable Chunk-ID via uuid5(NAMESPACE_URL, f"{file_path}::{symbol}::{occurrence_index}")**
`occurrence_index` is the 0-based index of the symbol name within the file (distinguishes
overloads and sub-chunks like `foo[0]` / `foo[1]`). `start_line` is removed from the key. Editing
lines ABOVE a symbol does not change its ID - no orphan Qdrant point is created by line shifts.

**D2 - Crash-safety via pending sentinel in the file_index table**
The `file_index` table receives a `status` column. Re-indexing a file: (a) write the row with
`status='pending'` + new sha BEFORE mutating Qdrant; (b) `delete_by_file` followed by upsert
of the new points; (c) set `status='done'`. In any run, a `'pending'` row is treated
as dirty and re-indexed. The one-shot migration of the 9 already-indexed repos uses BLUE/GREEN: index
into a new Qdrant collection, run the recall gate, promote (alias swap) only if it passes;
otherwise, keep the old collection. Normal incremental runs do NOT use blue/green.

**D3 - Walk scope = tracked only + check-ignore**
Use `git ls-files --cached` (WITHOUT `--others`), then filter each path through
`git check-ignore` so that files that were committed and later added to `.gitignore`
are also excluded. Untracked files require `git add` before being indexed.
Safety guarantee: no gitignored file is embedded. Add a test that commits
`.env`, adds it to `.gitignore`, and asserts it is never embedded.

**D4 - Reuse existing delete_by_file**
`vector_store.py:163` already has `delete_by_file(self, ctx: str, file_path: str)`. To delete
from all contexts, iterate over `COLLECTIONS` (which is `list(VALID_CONTEXTS)` in
`vector_store.py:24`). Do NOT add any new delete method. Remove any proposed code with
`delete_file_points`, `delete_by_file_path`, or `_collections()`.

**D5 - 14GB is a hypothesis, not a confirmed fact**
The probable cause is `graph_chunks: list[Chunk]` accumulating ALL chunks from ALL
files in `pipeline.py:141` before `build_dependency_records` runs at the end in
`pipeline.py:196-202` - not batch padding or the model. Remove any causal statement
about the 14GB. Treat as hypothesis to be confirmed by Phase 0 memory profiling.
The fix (streaming `build_dependency_records` per file instead of accumulating the full
list) belongs to this Spec A.

**D6 - Reconciliation is not blocked by hash-skip**
With D1 (stable IDs), files without a hash change keep valid Qdrant points even if
lines have shifted (no re-index needed on hash hit). A file with a modified hash
(hash miss) triggers `delete_by_file` + re-upsert, cleaning up points of deleted or renamed symbols.
That is how the orphan problem is resolved - the spec reflects this
and does not claim that delete-per-file only resolves orphans when a hash miss occurs.

---

## Context

### Three problems identified in code

**Problem 1 - Absence of chunk cap in Python/TypeScript/Markdown**

`chunker.py:37` defines `_MAX_CHUNK_LINES = 80`. Java applies this cap to methods
(`chunker.py:170-173`), to classes without methods (`chunker.py:141-145`), and to
records/enums/annotations (`chunker.py:107-109`). The function `_split_large_node`
(`chunker.py:217-241`) implements the split by 80-line stride and receives a `Node`
tree-sitter (not text lines - see section 1c below).

Python and TypeScript **do not apply this cap**. `_walk_python` (`chunker.py:311-360`) emits
one chunk per `function_definition` regardless of size. `_walk_ts` (`chunker.py:440-482`)
does the same for `function_declaration`, `method_definition`, and arrow functions via
`_ts_chunk_from_node` (`chunker.py:495-512`). Markdown and text fall through to the catchall of
`chunk_source` (`chunker.py:639-651`) and become **a single chunk** regardless of file size.

Consequence: a 1,000-line Python function produces a 1,000-line chunk, which consumes
far more of the model's token budget than the 80-line cap would allow and degrades
retrieval quality (less diversity per query).

**Memory hypothesis (to be confirmed by Phase 0):** it is likely that `graph_chunks: list[Chunk]`
(`pipeline.py:141`) accumulates these large chunks in memory until `build_dependency_records`
(`pipeline.py:196-202`) runs at the end, causing RSS spikes in large repos. This hypothesis
**has not yet been confirmed by profiling** - see D5.

**Problem 2 - Double parse during call-edge extraction**

`pipeline.py:196-197` calls `build_dependency_records(graph_chunks)`. Inside
`graph_extractor.py:57-76`, for each chunk, `extract_calls(chunk)` re-parses the
`chunk.content` field from scratch: Python via `ast.parse` (`graph_extractor.py:94`) and Java/TypeScript
via `parser.parse(source.encode("utf-8"))` (`graph_extractor.py:128`). The chunker already
built these trees in `_chunk_python` (`chunker.py:300`) and `_chunk_typescript`
(`chunker.py:429`) but discarded the root `Node` after building the chunks.

Result: each file is parsed twice - once in `chunker.py` to extract symbols,
again in `graph_extractor.py` to extract call-edges. The second parse is over `chunk.content`
(a text fragment, not the entire file), which also limits call-edge accuracy.

**Problem 3 - rglob without gitignored filtering**

`pipeline.py:70` uses `target.rglob("*")` and then filters each path by `path.parts`
(`pipeline.py:71`). This filter covers known directories (`.venv`, `node_modules`, etc.)
but does not respect `.gitignore`.

**The main issue is a security one** (D3): rglob may include files that
`.gitignore` excludes (`.env`, `secrets.json`, private keys). If these files have
`.py` or `.ts` extension they are embedded in Qdrant. This is a **privacy/security vulnerability**
that the switch to `git ls-files` resolves as its primary effect. The
performance benefit (avoiding traversal of `.venv`, `node_modules`) is secondary and should only
be declared as such after Phase 0 profiling confirms that `rglob` is a measurable bottleneck.

Note: `indexer.py:71-89` has an existing implementation of `_iter_repo_files` that uses
`git ls-files --cached --others --exclude-standard`. This implementation includes `--others`
(untracked) and does not filter by `git check-ignore`. The new `iter_git_files` will use only
`--cached` and will apply `git check-ignore` (see D3 and section 3a below).

Facts verified in code:
- `chunker.py:13-15`: `ChunkType = Literal["method", "constructor", "function", "class", "interface", "enum", "annotation", "record"]`
- `chunker.py:37` (`_MAX_CHUNK_LINES = 80`), applied in Java at `:170-173`, `:107-109`, `:141-145`
- `chunker.py:217-241` (`_split_large_node`, receives tree-sitter `Node`, not text lines)
- `chunker.py:300` (`_PY_PARSER.parse` discarded after chunking)
- `chunker.py:311-360` (Python `_walk_python`: no size cap)
- `chunker.py:417-437` (`_chunk_typescript`, parse at line 429)
- `chunker.py:440-482` (TypeScript `_walk_ts`: no size cap)
- `chunker.py:495-512` (`_ts_chunk_from_node`, returns single `Chunk`)
- `chunker.py:613-651` (`chunk_source` dispatcher; catchall lines 639-651)
- `graph_extractor.py:57-76` (`build_dependency_records` receives already-built chunks)
- `graph_extractor.py:79-89` (`extract_calls` per chunk)
- `graph_extractor.py:92-105` (`_extract_python_calls` calls `ast.parse` at line 94)
- `graph_extractor.py:127-128` (tree-sitter second parse Java/TS)
- `pipeline.py:59-75` (`iter_supported_files` with `rglob('*')` at line 70)
- `pipeline.py:141` (`graph_chunks: list[Chunk]` accumulates in memory until the end)
- `pipeline.py:196-202` (sequential loop `build_dependency_records` + `upsert_deps`)
- `pipeline.py:206-211` (current `_chunk_id` uses `start_line` - D1 requires changing to `occurrence_index`)
- `vector_store.py:24` (`COLLECTIONS = list(VALID_CONTEXTS)`)
- `vector_store.py:163` (`delete_by_file(self, ctx: str, file_path: str)` - ALREADY EXISTS, takes `ctx` as first arg)
- `indexer.py:71-89` (`_iter_repo_files` with `git ls-files --cached --others --exclude-standard`)

---

## Implementation decisions

| Topic | Decision |
|---|---|
| Chunk cap | `_MAX_CHUNK_LINES = 80` applied to **all** languages: Python, TypeScript, Markdown, text. Existing `_split_large_node` reused for Python/TS (via Node). New `_split_lines_into_chunks` function for Markdown and text. |
| Markdown per section | `.md` is tokenized by heading (`# / ## / ###`); each section becomes a chunk with `chunk_type = "section"` (new type - see section on ChunkType); if the section exceeds 80 lines, applies `_split_lines_into_chunks`. |
| Single parse | The chunker stores the tree in `Chunk.metadata["_tree"]`; `graph_extractor` reuses the tree instead of re-parsing. Tree discarded after call-edge extraction (not persisted to Qdrant). NOTE: the tree-sitter tree in `metadata["_tree"]` **is not thread-safe**; must be cleared before any parallel phase (see note in section 2c and handoff to Spec C). |
| File-walk | `iter_supported_files` replaced by a wrapper for `git ls-files --cached` with `git check-ignore` (D3). Fallback to rglob only outside git repos, with documented limitation. |
| Gitignored security | No gitignored file is embedded. Security gate verifiable by automated test (see tests section). |
| Stable Chunk-ID | `_chunk_id` changes to `uuid5(NAMESPACE_URL, f"{file_path}::{symbol}::{occurrence_index}")`. `occurrence_index` is the 0-based index of the symbol name within the file. DROP `start_line` from the key (D1). |
| Chunk orphans | With stable IDs (D1), files without content change do not produce orphans from line shifts. A file with a modified hash triggers `delete_by_file` (looping over `COLLECTIONS`) + re-upsert, cleaning up orphans of deleted or renamed symbols (D4, D6). |
| Crash-safety | `status` column in `file_index` with `pending` sentinel (D2). One-shot migration of 9 repos via BLUE/GREEN with recall gate before promoting (D2). |
| _walk_calls_ts_tree for Python | `graph_extractor` currently uses `ast.parse` for Python. Integrating the tree-sitter tree requires a new `_walk_calls_ts_tree` that operates on `tree_sitter.Tree`. This function is isolated as a sub-item of PR 2 - if complexity is high, split into its own PR with a dedicated spec section. |
| Post-split recall | The 80-line cap splits large functions into sub-chunks. Impact on recall (context fragmentation) is unknown. The recall harness must be run with the new chunker on the golden set BEFORE merging, as part of the gate (see recall-guard section). |
| Python cap design | Check the size BEFORE appending the chunk and call `_split_large_node` when exceeding the cap. The pop-and-replace pattern (modifying the list retroactively) is discarded. |
| Delivery order | (1) chunk cap + markdown + D1 (chunk-ID); (2) single parse + _walk_calls_ts_tree; (3) git ls-files + D2 (pending sentinel) + D3 (check-ignore) + one-shot migration. Each item is an independent PR with passing recall-guard. |
| YAGNI | Parallelism and GPU are not part of this spec. They are candidates for Spec C and Spec B respectively, dependent on the Phase 0 gate. |

---

## Components and changes

### 1. Universal chunk cap in `chunker.py`

**1a. Python - add size check in `_walk_python`**

In `_walk_python` (`chunker.py:311-360`), before appending the chunk for `function_definition`,
check the size. If it exceeds `_MAX_CHUNK_LINES`, pass the `Node` to `_split_large_node`
(which already exists and accepts `Node`) instead of creating a single `Chunk`:

```python
# chunker.py - inside _walk_python, when processing function_definition (line ~326)
if node.type in ("function_definition",):
    symbol = _python_node_identifier(node)
    start = node.start_point[0] + 1
    end = node.end_point[0] + 1
    chunk_type: ChunkType = "method" if in_class else "function"
    if (end - start + 1) > _MAX_CHUNK_LINES:
        chunks.extend(
            _split_large_node(
                node,
                source.encode("utf-8"),
                symbol or Path(file_path).stem,
                chunk_type,
                file_path,
            )
        )
    else:
        chunks.append(
            Chunk(
                symbol=symbol or Path(file_path).stem,
                chunk_type=chunk_type,
                start_line=start,
                end_line=end,
                content="\n".join(lines[node.start_point[0] : node.end_point[0] + 1]),
                file_path=file_path,
                language="python",
            )
        )
    # Recursion into inner functions continues as usual
```

Note: `_split_large_node` (`chunker.py:217-241`) receives `source: bytes` and decodes via
`source[node.start_byte : node.end_byte].decode(errors="replace")`. The call above passes
`source.encode("utf-8")` as convention - confirm in the implementation that `_chunk_python`
provides `source` as bytes or adjust the signature.

**1b. TypeScript - modify `_ts_chunk_from_node` (`chunker.py:495-512`)**

Change the signature of `_ts_chunk_from_node` to return `list[Chunk]`. If
`end - start + 1 > _MAX_CHUNK_LINES`, return `_split_large_node(node, ...)`. Otherwise,
return `[single_chunk]`. Adapt `_walk_ts` to use `chunks.extend(...)` instead of `chunks.append(...)`.

**1c. `_split_lines_into_chunks` - new helper function for Markdown and text**

`_split_large_node` (`chunker.py:217-241`) requires a tree-sitter `Node` and cannot be
used for Markdown (no tree-sitter parse) or for plain text. A new private function
accepts only text lines:

```python
def _split_lines_into_chunks(
    lines: list[str],
    start_line_1based: int,
    symbol: str,
    chunk_type: ChunkType,
    file_path: str,
    language: str,
) -> list[Chunk]:
    """Split a list of lines into sub-chunks of _MAX_CHUNK_LINES each."""
    result = []
    for i in range(0, len(lines), _MAX_CHUNK_LINES):
        part = lines[i : i + _MAX_CHUNK_LINES]
        idx = i // _MAX_CHUNK_LINES
        result.append(Chunk(
            symbol=f"{symbol}[{idx}]",  # ALL sub-chunks named symbol[idx], including idx=0
            chunk_type=chunk_type,
            start_line=start_line_1based + i,
            end_line=start_line_1based + i + len(part) - 1,
            content="\n".join(part),
            file_path=file_path,
            language=language,
        ))
    return result
```

Relationship to `_split_large_node` (DRY): `_split_large_node` operates on bytes of a
tree-sitter `Node` and is optimized for the Java/Python/TS case (it has access to the node's `start_point`).
`_split_lines_into_chunks` operates on already-extracted text lines and is necessary for
Markdown and plain text, where there is no tree-sitter tree. There is no real logic duplication -
the two functions operate on different input types. If in the future `_split_large_node`
is refactored to accept lines (extracting content externally), the two can be unified; for
now, document the reason for the separation in the docstring of both.

**1d. ChunkType - add 'section' for Markdown sections**

The current definition (`chunker.py:13-15`) is:
```python
ChunkType = Literal[
    "method", "constructor", "function", "class", "interface", "enum", "annotation", "record"
]
```

Using `chunk_type = "class"` for Markdown sections is semantic pollution (a Markdown
heading is not a class). Add `"section"` to the Literal:

```python
ChunkType = Literal[
    "method", "constructor", "function", "class", "interface",
    "enum", "annotation", "record", "section"
]
```

`chunk_type = "section"` is used only by `_chunk_markdown`. Verify that
`_CHUNK_TYPE_TO_SYMBOL` in `indexer.py:25-34` and the `chunk_type` field in `VectorStore.Chunk`
(`vector_store.py:32`) accept the new value. `VectorStore.Chunk.chunk_type` is `str`, so
no change needed. For `_CHUNK_TYPE_TO_SYMBOL`: the reason `"section"` does not collide with it today
is that `_symbols_for_file` in `indexer.py` only processes `.py` and `.java` files via
`_INDEXED_LANGUAGES`; Markdown chunks with `chunk_type="section"` never reach
`_CHUNK_TYPE_TO_SYMBOL`. If Markdown is added to `_INDEXED_LANGUAGES` in the future,
`_CHUNK_TYPE_TO_SYMBOL` will need to be updated to include `"section"`.

**1e. Markdown per section - new function `_chunk_markdown`**

New branch in `chunk_source` (`chunker.py:613-651`) for `language == "markdown"`:

```python
elif language == "markdown":
    return _chunk_markdown(source, file_path)
```

`_chunk_markdown` logic:
- Splits the file by lines.
- Detects headings: lines starting with `#` (up to `######`).
- Each section (heading to the next heading of equal or higher level) becomes a chunk
  with `symbol = <normalized heading text>`, `chunk_type = "section"`, `language = "markdown"`.
- Content before the first heading becomes a chunk with `symbol = Path(file_path).stem`.
- If the section exceeds `_MAX_CHUNK_LINES`, applies `_split_lines_into_chunks`.
- A file with no headings falls back to the current behavior (entire file as 1 chunk),
  but now with the cap applied via `_split_lines_into_chunks`.

**1f. Text (`.txt`) - apply cap in catchall**

When `language` does not match any handled case (including `"text"`), the current catchall
(`chunker.py:639-651`) returns the entire file as 1 chunk. Replace with a call to
`_split_lines_into_chunks` with `symbol = Path(file_path).stem` and `chunk_type = "section"`.

### 2. Single parse in `graph_extractor.py`

The least invasive approach is to store the tree-sitter tree in `Chunk.metadata["_tree"]`
during chunking and consume it in `extract_calls`. The tree is not serialized (it does not
reach Qdrant - the `metadata` field of `VectorChunk` is built explicitly in
`pipeline.py:107-120` and `pipeline.py:171-184`; no `**chunk.metadata` is passed).

**2a. Change in `chunker.py`**

In `_chunk_python` (`chunker.py:286-308`), after `tree = _PY_PARSER.parse(source.encode("utf-8"))`
(line 300), propagate the tree to each Chunk via `metadata={"_tree": tree}`. For chunks
derived from `_split_large_node`, the tree is the same as the parent file.

In `_chunk_typescript` (`chunker.py:417-437`), after `tree = parser.parse(source.encode("utf-8"))`
(line 429), same.

For Java in `chunk_source` (`chunker.py:618-634`), where `tree = _PARSER.parse(source_bytes)`,
propagate `metadata={"_tree": tree}` in each returned chunk.

**2b. New function `_walk_calls_ts_tree` in `graph_extractor.py`**

The current `_extract_python_calls` (`graph_extractor.py:92-105`) uses `ast.parse` internally.
The tree stored in `Chunk.metadata["_tree"]` by the Python chunker is a
`tree_sitter.Tree` (not `ast.Module`). Therefore, integrating the Python tree cache requires
a new function `_walk_calls_ts_tree(tree: tree_sitter.Tree) -> list[str]` that:
- Visits nodes of type `call_expression` and `method_invocation` in the Python tree-sitter tree.
- Extracts the callee name analogously to `_walk_calls` (`graph_extractor.py:145-167`).
- Returns a list of names filtered by `_SKIP_CALLS`.

Scope of nodes to visit in the tree-sitter-python tree:
- `call`: the callee name is in the first child or in the `function` field.
- `attribute`: for method calls like `obj.method()`, extract the attribute name.

The new function must have its own unit test (see tests section - `test_walk_calls_ts_tree`).
If integration complexity is high, extract into its own PR with a dedicated spec section
before merging with the rest of item 2.

Updated `extract_calls` (`graph_extractor.py:79-89`):

```python
def extract_calls(chunk: Chunk) -> list[str]:
    cached_tree = chunk.metadata.get("_tree")
    if chunk.language == "python":
        if cached_tree is not None:
            calls = _walk_calls_ts_tree(cached_tree)
        else:
            calls = _extract_python_calls(chunk.content)  # fallback: re-parse via ast
    elif chunk.language == "java":
        if cached_tree is not None:
            calls: set[str] = set()
            _walk_calls(cached_tree.root_node, calls)
        else:
            calls = _extract_ts_or_java_calls(chunk.content, _JAVA_CALL_PARSER)
    elif chunk.language in {"typescript", "ts"}:
        parser = _TSX_PARSER if chunk.file_path.endswith(".tsx") else _TS_PARSER
        if cached_tree is not None:
            calls = set()
            _walk_calls(cached_tree.root_node, calls)
        else:
            calls = _extract_ts_or_java_calls(chunk.content, parser)
    else:
        calls = []
    return sorted(call for call in calls if call != chunk.symbol)
```

**2c. Memory cleanup and thread-safety note**

After `build_dependency_records(graph_chunks)` returns (`pipeline.py:197`), clear
`chunk.metadata["_tree"]` from each chunk in `graph_chunks`:

```python
for chunk in graph_chunks:
    chunk.metadata.pop("_tree", None)
```

**THREAD-SAFETY NOTE FOR SPEC C:** the tree-sitter tree stored in
`Chunk.metadata["_tree"]` is not thread-safe. Any parallel phase introduced by Spec C
**cannot** access `metadata["_tree"]` from chunks concurrently. The correct handoff is:
`_tree` cleanup must occur before any Spec C step that parallelizes over
`graph_chunks`. Document explicitly in the cleanup comment in `pipeline.py`.

**2d. Memory and streaming of build_dependency_records (D5)**

The memory fix with the greatest expected impact is streaming `build_dependency_records` per file
instead of accumulating the entire `graph_chunks` list in memory. Implementation proposal:

```python
# pipeline.py - inside the index_path loop, right after the file's upsert_batch
if graph_store is not None:
    for record in build_dependency_records(chunks):  # chunks of the current file, not accumulated
        await graph_store.upsert_deps(
            record.symbol, calls=record.calls, called_by=record.called_by,
        )
```

Consequence: `graph_chunks` is no longer necessary; remove the variable and the block at the end
of the loop. This eliminates the retention of all chunks in memory simultaneously.

**SEMANTIC CAVEAT - loss of called_by edges across files:** `build_dependency_records`
aggregates `called_by` over ALL chunks passed in the argument. When called per file, only the
chunks of the current file are seen; calls from file B to a symbol in file A will not result
in `called_by` in A's record. The dependency graph will lose `called_by` edges between different files.
Evaluate the impact on graph quality during Phase 0 before merging this fix (gated by D5).

**This is a fix hypothesis** - Phase 0 must confirm via profiling whether `graph_chunks` is
indeed the main cause of the RSS spike before merging this change (D5).

### 3. File-walk via `git ls-files` in `pipeline.py`

**3a. New function `iter_git_files` in `axon/repo/file_walk.py`**

Extract as a public function in a new module `axon/repo/file_walk.py`:

```python
# axon/repo/file_walk.py
def iter_git_files(
    root: Path,
    *,
    suffixes: set[str],
) -> list[Path]:
    """List tracked files respecting .gitignore (D3).

    Uses `git ls-files --cached` to list only committed files.
    Filters each path through `git check-ignore` to exclude files that
    were committed and later added to .gitignore.

    Untracked files are not returned; they require
    `git add` before being indexed.

    SECURITY GUARANTEE: no gitignored file is returned.

    Falls back to rglob when `git` is not available or `root` is not
    a git repo. The fallback applies EXCLUDED_DIR_NAMES but does NOT guarantee
    exclusion of gitignored files - this limitation must be documented
    in the caller.
    """
```

The implementation uses `subprocess.run(["git", "-C", str(root), "ls-files", "--cached"], ...)` to
list tracked files and `subprocess.run(["git", "-C", str(root), "check-ignore", "--stdin"],
input="\n".join(paths))` to filter those added to `.gitignore` post-commit.

**3b. Modify `iter_supported_files` in `pipeline.py:59-75`**

Replace the body with a call to `iter_git_files` when `target` is a directory:

```python
def iter_supported_files(target: Path, *, languages: set[str] | None = None) -> Iterable[Path]:
    if target.is_file():
        language = _language_for_suffix(target.suffix)
        if language and (languages is None or language in languages):
            yield target
        return

    suffixes = {s for s, lang in _LANGUAGE_MAP.items()
                if languages is None or lang in languages}
    yield from iter_git_files(target, suffixes=suffixes)
```

**3c. Preserve fallback**

`iter_git_files` falls back to `rglob` when `git` is not available (same current behavior
for directories outside git repos), but documents that the fallback does not guarantee exclusion
of gitignored files. Outside git repos the security guarantee does not apply.

**3d. Update `indexer.py:71-89`**

`_iter_repo_files` in `indexer.py:71-89` uses `git ls-files --cached --others
--exclude-standard` (includes untracked, does not apply `check-ignore`). After creating
`iter_git_files`, replace the local implementation with a call to `iter_git_files` from
`axon.repo.file_walk`. This applies D3 consistently at all file-walk entry points in the system.

### 4. Stable Chunk-ID (D1)

Change `_chunk_id` in `pipeline.py:206-211`:

```python
# BEFORE (current)
def _chunk_id(path: Path, chunk: Chunk) -> str:
    key = f"{path}::{chunk.symbol}::{chunk.start_line}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))

# AFTER (D1)
def _chunk_id(file_path: str, symbol: str, occurrence_index: int) -> str:
    """Stable ID: does not change when lines above the symbol are edited.

    occurrence_index: 0-based index of the symbol name within the file,
    to distinguish overloads and sub-chunks (foo[0], foo[1]).
    """
    key = f"{file_path}::{symbol}::{occurrence_index}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))
```

The `occurrence_index` is computed by the caller when iterating over chunks of a file,
grouping by `chunk.symbol` and incrementing the index at each repetition.

### 5. Crash-safety via pending sentinel (D2)

The `file_index` table (Spec B scope - SQLite schema migration) receives column `status TEXT NOT NULL DEFAULT 'done'`.

File re-index sequence (pipeline.py):

```
1. UPDATE file_index SET status='pending', sha=<new_sha> WHERE file_path=<path>
   (or INSERT with status='pending' if the row does not exist)
2. await store.delete_by_file(ctx, str(file_path.resolve()))  # for each ctx in COLLECTIONS
3. chunks = chunk_source(...)
4. await store.upsert_batch(vector_chunks)
5. UPDATE file_index SET status='done' WHERE file_path=<path>
```

If the process dies between steps 2 and 5, on the next run the row will have `status='pending'`
and the file will be re-indexed from scratch, restoring consistency.

**One-shot migration of 9 repos (blue/green):**

1. Create new Qdrant collection with suffix `_v2` (e.g., `knowledge_v2`).
2. Run `axon index <vault_root>` targeting the `_v2` collections.
3. Run recall gate: score >= 0.90 on all new collections.
4. If passed: do alias swap (promote `_v2` as the main collection).
5. If failed: keep old collections; investigate regression before promoting.

Normal incremental runs after migration do NOT use blue/green - only the pending sentinel.

### 6. Per-file reconciliation (D4, D6)

`vector_store.py:163` already has `delete_by_file(self, ctx: str, file_path: str)`. To delete
from all contexts when re-indexing a file:

```python
# pipeline.py - inside the index_path loop, after hash miss, before upsert
for ctx in COLLECTIONS:
    await store.delete_by_file(ctx, str(file_path.resolve()))
```

The condition `if graph_store is not None or True:` present in previous drafts is
**dead code** (the `or True` makes the condition always true). The correct condition is
simply `for ctx in COLLECTIONS: ...` without conditional, because delete-before-upsert
must always occur on hash miss.

---

## Data flow (after)

```
iter_git_files(root)          # git ls-files --cached + check-ignore; no rglob over .venv/.git
  -> [file_path, ...]
    for each file with hash miss:
      (1) write file_index status='pending'  # crash-safety D2
      (2) delete_by_file(ctx, path)          # for each ctx in COLLECTIONS (D4)
      chunk_source(source, lang, path)
        _walk_python / _walk_ts / _chunk_markdown
          -> chunks with metadata["_tree"] = parsed tree
      embed(chunks)
      upsert_batch(vector_chunks)            # no _tree in the Qdrant payload
      (3) write file_index status='done'     # crash-safety D2
      build_dependency_records(file_chunks)  # stream per-file (hypothetical D5 fix)
        extract_calls(chunk)
          uses chunk.metadata["_tree"]       # no second parse
        upsert_deps(...)
      clears metadata["_tree"] from chunks   # before any parallel phase (Spec C)
```

Note: `VectorChunk` in `pipeline.py:107-120` and `pipeline.py:171-184` builds fields
explicitly; the `Chunk` `metadata` (which contains `_tree`) **is not copied** to the Qdrant
payload. Confirm in the implementation that no `**chunk.metadata` is passed to `VectorChunk`.

---

## Success criteria (numeric, per machine)

| Metric | R7 5800X3D (desktop) | M1 Pro (mac) | How to measure |
|---|---|---|---|
| Full index wall time (9 repos, cold hash-cache) | <= 5 min | <= 8 min | `time axon index <vault>`, 3 runs, median |
| Incremental refresh wall time (1 file, 10-50 chunks) | <= 10 s | <= 15 s | 5 files of varying sizes, all must pass |
| Post-commit hook wall time (20 files) | <= 30 s | <= 45 s | 3 runs, take the maximum |
| Peak RSS during full index | <= 2 GB | <= 1.5 GB | psutil sampling every 2 s |
| Throughput (chunks/s end-to-end) | >= 300 | >= 200 | fixed synthetic corpus of 500 Python functions of 15-30 lines |
| Recall Top-1 (20 queries golden set) | >= 0.90 | >= 0.90 | recall-guard harness (see section below) |
| Recall Top-3 (20 queries golden set) | >= 0.95 | >= 0.95 | same harness |
| Gitignored files embedded | 0 Qdrant points | 0 Qdrant points | Qdrant scroll post-index, check `.env` and `secrets.json` |
| Orphan points after editing 3 lines above a symbol | 0 orphan points | 0 orphan points | scroll by file_path before and after the edit |

**Phase 0 gate (mandatory before any PR from this spec):**

1. `benchmarks/phase0_baseline.json` committed with all fields filled.
2. `recall_top1_baseline >= 0.80` and `recall_top3_baseline >= 0.80` in the pre-overhaul baseline.
   If the baseline is below 0.80, the 0.90 floor in the criteria above is **aspirational**
   and must be documented as such in the JSON.
3. At least one bottleneck confirmed with a measured number (not an estimate) and ranked by
   contribution to total wall time.
4. RSS hypothesis confirmed or refuted by profiling (D5): record whether `graph_chunks`
   is indeed the main cause of the spike, or identify the real cause.
5. `gpu_available` recorded; if `false`, GPU is removed from the Spec C plan entirely.
6. `stale_qdrant_points_confirmed` recorded; if `true`, delete-before-upsert is item 0 of the PR.

---

## Recall-guard (semantic quality)

Before any change in chunker, embedder, or pipeline:

1. **Golden set** - 20 triples `(query, expected_file_path, expected_symbol)` stored in
   `tests/recall/golden_set.json`. Distribution: 8 Python queries, 5 Java, 4 TypeScript, 3
   cross-file. File is immutable by code; only updated by explicit human decision with a
   separate commit.

2. **Harness** - `RecallBenchmarkFixture` mirrors the shape of `RetrievalBenchmarkFixture`
   (`src/axon/benchmark/retrieval.py`). Uses a real Qdrant via `testcontainers[qdrant]` (already in
   dev extras of `pyproject.toml`). Indexes the reference corpus (`src/axon/embedder/`,
   `src/axon/store/`) in a fresh container before running the queries.

3. **Checks per query** (reuses `BenchmarkCheck` from `src/axon/benchmark/contracts.py`):
   - `top_1_file_match`: `hits[0].payload["file_path"] == expected_file`
   - `top_3_file_match`: `expected_file` in `{hits[0..2].payload["file_path"]}`
   - `min_score`: `hits[0].score >= min_score` (minimum 0.70)
   - `symbol_match`: `hits[0].payload["symbol"] == expected_symbol`

4. **Regression gate** - pytest loads `tests/recall/baseline.json` and asserts:
   - `len(report.regressions) == 0`: no query that passed in the baseline can fail after
     the change (no regression per individual query vs baseline).
   - `BenchmarkRunSummary.score >= 0.90`: aggregate post-change score.
   If the recorded baseline is < 0.90, the 0.90 floor is treated as aspirational and the gate
   only requires absence of regression vs baseline (does not reject the PR solely for score < 0.90).

5. **Recall is the entry gate for PR 1 (chunk cap):** the 80-line cap splits large functions
   into sub-chunks. The impact on recall (potential context fragmentation) is unknown. The harness
   **must be run with the new chunker on the golden set BEFORE merging PR 1**, and the result
   must be added to `benchmarks/phase0_baseline.json` as `recall_post_split`. If there is a
   regression (`regressions != []` or score falls below baseline), the PR is blocked pending investigation.

6. **Embedding in subprocess** - if Phase 0 profiling refutes the `graph_chunks` accumulation
   hypothesis and the RSS spike comes from model loading, the harness must run the
   embedding in a separate subprocess (collects result via stdout JSON) to avoid contaminating
   the benchmark process.

---

## Security test: gitignored files never embedded (D3)

**Fixture** (`tests/test_file_walk_security.py`):

```python
@pytest.mark.asyncio
async def test_gitignored_files_never_embedded(tmp_path):
    # Arrange: git repo with .env and secrets.json in .gitignore
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True)
    (repo / ".gitignore").write_text(".env\nsecrets.json\n")
    (repo / ".env").write_text("SECRET_KEY=abc123\n")
    (repo / "secrets.json").write_text('{"password": "hunter2"}\n')
    (repo / "main.py").write_text("def hello(): pass\n")
    subprocess.run(["git", "-C", str(repo), "add", ".gitignore", "main.py"], check=True)

    # Act: list files via iter_git_files
    from axon.repo.file_walk import iter_git_files
    files = iter_git_files(repo, suffixes={".py", ".env", ".json"})

    # Assert: .env and secrets.json absent; main.py present
    paths = {f.name for f in files}
    assert ".env" not in paths
    assert "secrets.json" not in paths
    assert "main.py" in paths


@pytest.mark.asyncio
async def test_committed_then_gitignored_never_embedded(tmp_path):
    """A file that was committed and later added to .gitignore must not be embedded.

    This is the critical D3 case: without `git check-ignore`, a file that passed
    through `git ls-files --cached` would still appear in the listing even after being gitignored.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@test.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)

    # Commit .env WITHOUT gitignore
    (repo / ".env").write_text("SECRET_KEY=abc123\n")
    (repo / "main.py").write_text("def hello(): pass\n")
    subprocess.run(["git", "-C", str(repo), "add", ".env", "main.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "initial"], check=True)

    # Add .env to .gitignore post-commit
    (repo / ".gitignore").write_text(".env\n")
    subprocess.run(["git", "-C", str(repo), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "gitignore"], check=True)

    # Act: list files via iter_git_files
    from axon.repo.file_walk import iter_git_files
    files = iter_git_files(repo, suffixes={".py", ".env"})

    # Assert: .env is in git ls-files --cached but must be excluded by check-ignore
    paths = {f.name for f in files}
    assert ".env" not in paths, ".env was committed but is gitignored: must not be embedded"
    assert "main.py" in paths
```

Both tests are **security gates**: failing is blocking for merge. They must run in
CI without external dependencies (only `git` in PATH).

---

## Units (isolation)

| Unit | File | Dependencies | Testable in isolation? |
|---|---|---|---|
| `_split_lines_into_chunks` | `chunker.py` | none (pure function) | yes - input: lines, output: list of Chunk |
| `_chunk_markdown` | `chunker.py` | `_split_lines_into_chunks` | yes - input: markdown string |
| Cap in `_walk_python` | `chunker.py` | `_split_large_node` (existing) | yes - Python function >80 lines must generate N chunks |
| Cap in `_walk_ts` / `_ts_chunk_from_node` | `chunker.py` | `_split_large_node` (existing) | yes |
| `_walk_calls_ts_tree` (new) | `graph_extractor.py` | `tree_sitter.Tree` from Python | yes - pre-parsed tree via `_PY_PARSER.parse` |
| `extract_calls` with tree cache | `graph_extractor.py` | `Chunk.metadata["_tree"]` | yes - mock chunk with pre-parsed tree |
| `iter_git_files` | `axon/repo/file_walk.py` | subprocess `git` | yes with tmp_path + git init |
| updated `iter_supported_files` | `pipeline.py` | `iter_git_files` | yes - inject `iter_git_files` mock |
| `delete_by_file` (existing in `vector_store.py:163`) | `vector_store.py` | Qdrant client | yes with testcontainers[qdrant] |
| Per-file reconciliation in `index_path` | `pipeline.py` | `store.delete_by_file` | yes - verify point count before/after |
| updated `_chunk_id` (D1) | `pipeline.py` | none | yes - pure function; test that IDs are equal for the same symbol even with different start_line |

---

## End-to-end verification

1. **Chunk cap**: index a Python file with a 200-line function; `chunk_source`
   must return 3 chunks (lines 1-80, 81-160, 161-200). Verify `start_line` and
   `end_line` of each chunk.

2. **Markdown per section**: index a `README.md` with 3 headings; verify that
   `chunk_source` returns 3+ chunks (one per section), none with more than 80 lines,
   each with `chunk_type == "section"`.

3. **Large headingless markdown file**: index a 500-line `.md` without `#`;
   verify that `chunk_source` returns 7 chunks of 80 lines (6) + 1 of 20 lines.

4. **Single parse**: instrument `ast.parse` and `parser.parse` in `graph_extractor.py`
   with a counter; index 10 Python files; counter must be 0 (all calls used the cached tree).

5. **git ls-files + check-ignore**: index repo with `.env` committed and later gitignored;
   scroll Qdrant and verify absence of points with `file_path` containing `.env`.

6. **Per-file reconciliation**: index a file with 2 functions; edit 3 lines before the
   first function (shifts start_line); re-index; `scroll(filter=file_path)` must
   return exactly 2 points (not 4). With D1, the chunk-ID does not change from line shifts.

7. **Stable Chunk-ID (D1)**: edit 3 lines above a symbol; re-index; verify that
   the `id` of the Qdrant point for that symbol did not change.

8. **Throughput**: synthetic corpus of 500 Python functions of 15-30 lines; throughput
   must be >= 300 chunks/s on desktop. Record in `benchmarks/phase0_baseline.json`
   as the denominator for gain calculations.

9. **Post-overhaul recall**: run `compare_benchmark_runs(current, baseline)` and verify
   `regressions == []` and `score >= 0.90` (or absence of regression vs baseline if baseline < 0.90).

---

## Tests

### Unit tests (no external I/O)

- `test_split_lines_into_chunks`: input of 200 lines -> 3 chunks; verifies `start_line`,
  `end_line`, `symbol` with suffix `[0]`/`[1]`/`[2]`.
- `test_chunk_python_size_cap`: 100-line Python function -> 2 chunks; 79-line function
  -> 1 chunk (below cap).
- `test_chunk_typescript_size_cap`: same pattern for `.ts`.
- `test_chunk_markdown_with_headers`: markdown with 3 headings -> 3+ chunks, none > 80
  lines, all with `chunk_type == "section"`.
- `test_chunk_markdown_no_header_large`: 500 lines without heading -> 7 chunks of <=80 lines.
- `test_chunk_text_large`: 160-line `.txt` -> 2 chunks of 80 lines.
- `test_chunk_source_dispatcher_markdown`: `chunk_source(source, "markdown", path)` must
  not return a chunk with `end_line - start_line + 1 > 80`.
- `test_chunk_type_section_valid`: `Chunk(chunk_type="section", ...)` must be accepted by
  the `ChunkType` `Literal` without raising a Pydantic validation error.
- `test_walk_calls_ts_tree_positive`: pass a Python tree-sitter tree with known calls;
  `_walk_calls_ts_tree` must return the calls.
- `test_walk_calls_ts_tree_fallback`: `extract_calls` with a chunk without `metadata["_tree"]` must
  fall back via `ast.parse` and still return correct calls.
- `test_extract_calls_uses_cached_tree`: create a chunk with `metadata["_tree"]` filled by
  `_PY_PARSER.parse`; monkeypatch `ast.parse` to raise `AssertionError`; call
  `extract_calls`; must return calls without invoking `ast.parse`. Add a second
  case: chunk WITHOUT `_tree` in metadata; `ast.parse` must not raise (fallback works).
- `test_chunk_id_stable_across_line_shift`: generate two chunks with the same `symbol` and
  `occurrence_index` but different `start_line`; verify that `_chunk_id` returns the same UUID.
- `test_chunk_id_distinguishes_overloads`: two chunks with the same `symbol` and different
  `occurrence_index` must have different UUIDs.

### Integration (git + filesystem)

- `test_gitignored_files_never_embedded` (security gate - described in the section above).
- `test_committed_then_gitignored_never_embedded` (security gate - described in the section above).
- `test_iter_git_files_fallback_no_git`: directory without `.git`; `iter_git_files` must use
  rglob and return files with the correct suffixes.
- `test_delete_by_file_removes_stale_points`: with Qdrant via testcontainers, insert 3
  points for `file_path=A` in ctx `knowledge`, call `delete_by_file("knowledge", A)`,
  scroll returns 0 points.
- `test_reconcile_per_file_no_orphans`: index file (2 functions), edit, re-index;
  scroll by file_path returns exactly 2 points.

### Benchmarks / gate

- `test_recall_guard_no_regression`: loads `tests/recall/baseline.json`, runs harness,
  asserts `regressions == []` and `score >= 0.90` (or absence of regression if baseline < 0.90).
- `test_gitignored_never_embedded_integration`: start Qdrant via testcontainers, index repo
  with `.env` in `.gitignore`, scroll all points, assert 0 hits with `.env` in `file_path`.

### Minimum coverage and CI

Target coverage: 80%+ for new/changed units (`chunker.py`, `graph_extractor.py`,
`axon/repo/file_walk.py`, modified `pipeline.py` loop).

Configure `--cov-fail-under=80` in `pyproject.toml` only for the touched modules, not
the entire project (avoid coverage regression in other unmodified modules).
Example configuration in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
addopts = "--cov=axon.embedder.chunker --cov=axon.embedder.graph_extractor --cov=axon.repo.file_walk --cov=axon.embedder.pipeline --cov-fail-under=80"
```

Dedicated CI step: `pytest tests/test_chunker.py tests/test_graph_extractor.py tests/test_file_walk_security.py tests/test_pipeline.py --cov-fail-under=80`.

---

## Migration of 9 already-indexed repos (D2 - blue/green)

The `personal` / `knowledge` repos were indexed with the previous version of the chunker (no cap
for Python/TS, no markdown per section). After this spec is applied, chunk-IDs change (D1
swaps `start_line` for `occurrence_index` in the key), generating orphans in the existing points.

**One-shot migration procedure (pre-merge of the first PR from this spec - blue/green):**

1. Record `benchmarks/phase0_baseline.json` (Phase 0 gate complete).
2. Create Qdrant collections with suffix `_v2` for each entry in `COLLECTIONS`.
3. Run `axon index <vault_root>` targeting the `_v2` collections (destination collection flag).
4. Run recall gate on the `_v2` collections: score >= 0.90.
5. If passed: alias swap (promote `_v2` as the main collection).
6. If failed: keep old collections; investigate regression before promoting.
7. Verify: point count per collection before and after. If the count rises beyond expected
   (the cap generates more chunks for large files), that is normal and expected.

Normal incremental runs **after migration** do NOT use blue/green - only the pending
sentinel (D2) guarantees crash-safety.

---

## Out of scope

- I/O or CPU parallelism in the pipeline (Spec C).
- GPU / ONNX Runtime provider swap (Spec B, dependent on the GPU gate in Phase 0).
- Persistent hash cache across processes (Spec B - cacheable).
- `upsert_deps` Redis pipelining (Spec B or C).
- New languages (Go, Rust, Bash).
- Change to the embedding model or vector dimensions.
- Change to the `VectorChunk` structure or Qdrant schema (except the `ctx` of `VectorStore.Chunk`).
- SQLite schema migration for `file_index` - this is Spec B scope, but the pending sentinel (D2) depends on it; coordinate with Spec B or bring only the `status` column into this spec if needed.

---

## Assumptions to verify (before implementing)

| Assumption | Cheap verification |
|---|---|
| `rglob` is a measurable bottleneck in index wall time | `time python -c "list(Path('<vault>').rglob('*'))"` vs `time git ls-files <vault> | wc -l`; if rglob < 1 s, the benefit of git ls-files is **security only** (D3), not performance |
| `graph_chunks` is the main cause of the RSS spike | profiling with `psutil` or `tracemalloc` during full index; record in `benchmarks/phase0_baseline.json` (D5) |
| Large Python/TS functions exist in the 9 repos today | count chunks with end_line - start_line > 80 in the largest repo |
| tree-sitter tree is acceptable in `dict` via `Chunk.metadata` | test that `Chunk(metadata={"_tree": tree})` does not raise in `Chunk.model_validate` (Pydantic v2 accepts `Any` in `dict`) |
| `ast.parse` in `graph_extractor` is called on `chunk.content` (fragment) | confirm in `graph_extractor.py:94` that `source = chunk.content`, not `chunk.file_path.read_text()` - **CONFIRMED**: `_extract_python_calls(chunk.content)` in `extract_calls` line 81 |
| existing `delete_by_file` supports the required semantics | **CONFIRMED**: `vector_store.py:163` has `delete_by_file(self, ctx: str, file_path: str)`; to delete from all contexts, iterate over `COLLECTIONS` |

---

## Implementation notes

- Do not use em-dash or en-dash in any generated code comment or docstring.
- Preserve signature compatibility of `chunk_source(source, language, file_path)` -
  the public signature does not change.
- `_split_lines_into_chunks` is private (prefix `_`); do not export in `__init__.py`.
- The `metadata` field of `Chunk` is `dict = Field(default_factory=dict)` (`chunker.py:48`);
  Pydantic v2 accepts any value in `dict`. The tree-sitter tree is not JSON-serializable
  but is not persisted (only used in memory until the end of file processing).
- When clearing `metadata["_tree"]` after call-edge extraction, use
  `chunk.metadata.pop("_tree", None)` instead of direct assignment to avoid `KeyError`
  if the chunk comes from a language without a cached tree (markdown, text, fallback chunks).
- The `status` column in `file_index` requires coordination with Spec B (schema migration).
  If Spec B is delayed, include only the `status` column in this spec to unblock
  crash-safety without depending on full Spec B.
- `_walk_calls_ts_tree` must be added to `graph_extractor.py` and tested in isolation
  before integrating into the main `extract_calls` flow. If the implementation reveals
  unexpected complexity (difference in tree-sitter-python vs tree-sitter-java grammar),
  create a separate PR with a dedicated spec section.
