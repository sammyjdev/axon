# Axon Perf Overhaul A - Chunking, Linear Walk, Stable IDs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans when executing this plan. Steps use checkbox (- [ ]) syntax.

**Goal:** Implement the three orthogonal linear improvements from Spec A: (1) universal 80-line chunk-size cap covering Python, TypeScript, and Markdown-by-section; (2) parse-once linearization that stores the tree-sitter tree in `Chunk.metadata["_tree"]` and reuses it in `graph_extractor` to eliminate the second parse; (3) `git ls-files --cached` + `git check-ignore` file walk replacing `rglob` as a security fix; and (4) D1 stable chunk-ID using `uuid5(NAMESPACE_URL, f"{file_path}::{symbol}::{occurrence_index}")`. A 20-query recall golden set and harness are built as Task 1 and gate every subsequent task.

**Architecture:**
- `src/axon/embedder/chunker.py` gains `_split_lines_into_chunks`, `_chunk_markdown`, size-cap logic in `_walk_python` and `_ts_chunk_from_node`, and `"section"` in `ChunkType`.
- `src/axon/repo/file_walk.py` (new module) exports `iter_git_files(root, *, suffixes)`.
- `src/axon/embedder/pipeline.py` gets updated `_chunk_id` (D1), updated `iter_supported_files` (calls `iter_git_files`), and streaming `build_dependency_records` per-file (D5 gated on Phase 0 profiling confirming `graph_chunks` as RSS cause - confirmed debunked in `benchmarks/phase0_baseline.json`; keep current batch approach but stream per-file for correctness).
- `src/axon/embedder/graph_extractor.py` gains `_walk_calls_ts_tree` and an updated `extract_calls` that consumes `Chunk.metadata["_tree"]`.
- `src/axon/code/indexer.py` `_iter_repo_files` (lines 71-89) updated to call `iter_git_files`.
- `tests/recall/golden_set.json` + `tests/recall/baseline.json` + `tests/recall/test_recall_guard.py` created as gate infrastructure.
- `tests/test_file_walk_security.py` added as blocking security gate.

**Tech Stack:** Python 3.11+, tree-sitter (already installed), pytest + pytest-asyncio, testcontainers[qdrant] (already in dev extras), subprocess for git, uuid (stdlib), hashlib (stdlib).

## Global Constraints

- NEVER run `axon index`, embedding inference, or any command that loads the ONNX model or touches live Qdrant during plan execution. Tests that need Qdrant use `testcontainers[qdrant]`; unit tests are fully in-memory.
- `_chunk_id` EXACT signature: `_chunk_id(file_path: str | Path, symbol: str, occurrence_index: int) -> str` returning `str(uuid.uuid5(uuid.NAMESPACE_URL, f"{file_path}::{symbol}::{occurrence_index}"))`. No variants.
- `delete_by_file(ctx: str, file_path: str)` at `src/axon/store/vector_store.py:163` ALREADY EXISTS - reuse it, do not add any new delete method.
- `COLLECTIONS = list(VALID_CONTEXTS)` at `src/axon/store/vector_store.py:24` - use this when iterating contexts for delete.
- All file paths normalized via `Path(p).as_posix()` in file-cache operations.
- Output rule: only plain hyphens `-`, never em or en dashes in code, comments, or docstrings.
- `tests/recall/golden_set.json` is immutable by code; update only by explicit human decision in a separate commit.
- Phase 0 baseline (`benchmarks/phase0_baseline.json`) is already committed - it confirms: `graph_chunks` list is only 135MB (NOT the RSS culprit); GPU is 541 chunks/s; rglob switch is a security fix only; 0 chunk-id collisions so D1 migration is safe.

---

### Task 1: Recall golden set + harness (gate infrastructure)

**Files:**
- Create: `tests/recall/golden_set.json`
- Create: `tests/recall/baseline.json`
- Create: `tests/recall/test_recall_guard.py`
- Test path: `tests/recall/test_recall_guard.py`

**Interfaces:**
- Consumes: `src/axon/benchmark/contracts.py:BenchmarkCheck`, `BenchmarkRunSummary`, `BenchmarkResult`; `src/axon/embedder/chunker.py:chunk_source`; `src/axon/embedder/pipeline.py:iter_supported_files`; `src/axon/embedder/engine.py:EmbedderEngine`; `src/axon/store/vector_store.py:VectorStore`; `testcontainers[qdrant]`
- Produces: `tests/recall/baseline.json` (JSON with `recall_top1`, `recall_top3`, `results_by_query`); `RecallGuardFixture` dataclass; `run_recall_guard(golden_set_path, store, engine) -> BenchmarkRunSummary`

- [ ] **Step 1:** Create `tests/recall/` directory and `tests/recall/__init__.py` (empty).

  ```bash
  mkdir -p tests/recall && touch tests/recall/__init__.py
  ```

- [ ] **Step 2:** Create `tests/recall/golden_set.json` with 20 queries targeting symbols that exist in `src/axon/embedder/` and `src/axon/store/`. Distribution: 8 Python, 5 Java-style (none - axon has no Java; use 5 additional Python), 4 TypeScript (use TS in `src/axon/`), 3 cross-file. Use symbols confirmed present in `src/axon/embedder/chunker.py`, `src/axon/embedder/graph_extractor.py`, `src/axon/embedder/pipeline.py`, `src/axon/store/vector_store.py`.

  ```json
  [
    {"id": "q01", "query": "chunk python source tree-sitter walk function definition", "expected_file": "src/axon/embedder/chunker.py", "expected_symbol": "_walk_python", "min_score": 0.70},
    {"id": "q02", "query": "split large node into sub-chunks stride lines", "expected_file": "src/axon/embedder/chunker.py", "expected_symbol": "_split_large_node", "min_score": 0.70},
    {"id": "q03", "query": "embed texts vectors EmbedderEngine", "expected_file": "src/axon/embedder/pipeline.py", "expected_symbol": "ingest_file", "min_score": 0.70},
    {"id": "q04", "query": "upsert batch vector chunks qdrant", "expected_file": "src/axon/store/vector_store.py", "expected_symbol": "upsert_batch", "min_score": 0.70},
    {"id": "q05", "query": "delete file points from collection", "expected_file": "src/axon/store/vector_store.py", "expected_symbol": "delete_by_file", "min_score": 0.70},
    {"id": "q06", "query": "extract python calls ast walk call nodes", "expected_file": "src/axon/embedder/graph_extractor.py", "expected_symbol": "_extract_python_calls", "min_score": 0.70},
    {"id": "q07", "query": "build dependency records calls called_by symbols", "expected_file": "src/axon/embedder/graph_extractor.py", "expected_symbol": "build_dependency_records", "min_score": 0.70},
    {"id": "q08", "query": "chunk typescript tsx tree-sitter walk function declaration", "expected_file": "src/axon/embedder/chunker.py", "expected_symbol": "_walk_ts", "min_score": 0.70},
    {"id": "q09", "query": "chunk id stable uuid hash file path symbol", "expected_file": "src/axon/embedder/pipeline.py", "expected_symbol": "_chunk_id", "min_score": 0.70},
    {"id": "q10", "query": "iter supported files rglob language filter", "expected_file": "src/axon/embedder/pipeline.py", "expected_symbol": "iter_supported_files", "min_score": 0.70},
    {"id": "q11", "query": "walk ts callee name member expression identifier", "expected_file": "src/axon/embedder/graph_extractor.py", "expected_symbol": "_ts_callee_name", "min_score": 0.70},
    {"id": "q12", "query": "python fallback chunk single file parse error", "expected_file": "src/axon/embedder/chunker.py", "expected_symbol": "_python_fallback_chunk", "min_score": 0.70},
    {"id": "q13", "query": "vector store search cosine qdrant query points", "expected_file": "src/axon/store/vector_store.py", "expected_symbol": "search", "min_score": 0.70},
    {"id": "q14", "query": "extract calls chunk language java typescript python", "expected_file": "src/axon/embedder/graph_extractor.py", "expected_symbol": "extract_calls", "min_score": 0.70},
    {"id": "q15", "query": "index path hash cache file chunks embed", "expected_file": "src/axon/embedder/pipeline.py", "expected_symbol": "index_path", "min_score": 0.70},
    {"id": "q16", "query": "chunk source dispatcher java python typescript language", "expected_file": "src/axon/embedder/chunker.py", "expected_symbol": "chunk_source", "min_score": 0.70},
    {"id": "q17", "query": "ts chunk from node start end line content", "expected_file": "src/axon/embedder/chunker.py", "expected_symbol": "_ts_chunk_from_node", "min_score": 0.70},
    {"id": "q18", "query": "infer context from path vault root personal knowledge", "expected_file": "src/axon/embedder/pipeline.py", "expected_symbol": "infer_ctx_from_path", "min_score": 0.70},
    {"id": "q19", "query": "walk calls tree-sitter node method invocation java", "expected_file": "src/axon/embedder/graph_extractor.py", "expected_symbol": "_walk_calls", "min_score": 0.70},
    {"id": "q20", "query": "chunk java file tree-sitter extract methods constructors", "expected_file": "src/axon/embedder/chunker.py", "expected_symbol": "chunk_java_file", "min_score": 0.70}
  ]
  ```

- [ ] **Step 3:** Create `tests/recall/baseline.json` with placeholder values. This file is updated by the harness after the first real run. Do NOT run embedding now.

  ```json
  {
    "_note": "Populated by first run of test_recall_guard_populate_baseline. Do not edit manually.",
    "recall_top1": null,
    "recall_top3": null,
    "results_by_query": {}
  }
  ```

- [ ] **Step 4:** Write the failing test in `tests/recall/test_recall_guard.py`. The test at this stage only validates the golden set schema (no Qdrant needed) so it runs without embedding. The full harness test is marked `skip` until Task 4 (after chunker changes stabilize).

  ```python
  from __future__ import annotations

  import json
  from pathlib import Path

  import pytest

  GOLDEN_SET_PATH = Path(__file__).parent / "golden_set.json"
  BASELINE_PATH = Path(__file__).parent / "baseline.json"

  REQUIRED_QUERY_KEYS = {"id", "query", "expected_file", "expected_symbol", "min_score"}


  def test_golden_set_schema() -> None:
      """Golden set file exists and each entry has all required keys."""
      assert GOLDEN_SET_PATH.exists(), "golden_set.json missing"
      data = json.loads(GOLDEN_SET_PATH.read_text(encoding="utf-8"))
      assert len(data) == 20, f"expected 20 queries, got {len(data)}"
      for entry in data:
          missing = REQUIRED_QUERY_KEYS - set(entry.keys())
          assert not missing, f"entry {entry.get('id')} missing keys: {missing}"
          assert isinstance(entry["min_score"], float)
          assert 0.0 < entry["min_score"] <= 1.0


  def test_golden_set_no_duplicate_ids() -> None:
      data = json.loads(GOLDEN_SET_PATH.read_text(encoding="utf-8"))
      ids = [e["id"] for e in data]
      assert len(ids) == len(set(ids)), "duplicate IDs in golden_set.json"


  def test_baseline_json_exists() -> None:
      assert BASELINE_PATH.exists(), "baseline.json missing"
      data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
      assert "recall_top1" in data
      assert "recall_top3" in data
      assert "results_by_query" in data


  @pytest.mark.skip(reason="Full harness activated after Task 4 chunker changes are stable")
  def test_recall_guard_no_regression() -> None:
      """Activated in Task 4: asserts regressions == [] and score >= 0.90."""
      ...
  ```

- [ ] **Step 5:** Run the schema tests and confirm they pass.

  ```bash
  pytest tests/recall/test_recall_guard.py::test_golden_set_schema tests/recall/test_recall_guard.py::test_golden_set_no_duplicate_ids tests/recall/test_recall_guard.py::test_baseline_json_exists -v
  ```

  Expected: 3 passed, 1 skipped (the guard test).

- [ ] **Step 6:** Commit.

  ```bash
  git add tests/recall/golden_set.json tests/recall/baseline.json tests/recall/test_recall_guard.py tests/recall/__init__.py
  git commit -m "feat: add recall golden set (20 queries) + schema-validation harness"
  ```

---

### Task 2: D1 stable chunk-ID

**Files:**
- Modify: `src/axon/embedder/pipeline.py` lines 206-211 (`_chunk_id`), lines 109 (`ingest_file` call site), line 173 (`index_path` call site)
- Test path: `tests/embedder/test_pipeline_chunk_id.py` (new file)

**Interfaces:**
- Consumes: `src/axon/embedder/chunker.py:Chunk` (fields: `symbol`, `start_line`)
- Produces: `_chunk_id(file_path: str | Path, symbol: str, occurrence_index: int) -> str`; callers build an `occurrence_index` counter grouped by symbol name within each file

- [ ] **Step 1:** Write the failing test first.

  ```python
  # tests/embedder/test_pipeline_chunk_id.py
  from __future__ import annotations

  import uuid
  from pathlib import Path

  import pytest


  def _chunk_id(file_path: str | Path, symbol: str, occurrence_index: int) -> str:
      """Import target - will import from pipeline after implementation."""
      from axon.embedder.pipeline import _chunk_id as real
      return real(file_path, symbol, occurrence_index)


  def test_chunk_id_stable_across_line_shift() -> None:
      """Same file+symbol+occurrence_index must produce identical UUID regardless of start_line."""
      id_a = _chunk_id("src/foo.py", "my_func", 0)
      id_b = _chunk_id("src/foo.py", "my_func", 0)
      assert id_a == id_b


  def test_chunk_id_differs_by_occurrence_index() -> None:
      """Two occurrences of the same symbol name (overloads/sub-chunks) get different IDs."""
      id_a = _chunk_id("src/foo.py", "my_func", 0)
      id_b = _chunk_id("src/foo.py", "my_func", 1)
      assert id_a != id_b


  def test_chunk_id_differs_by_file() -> None:
      id_a = _chunk_id("src/a.py", "func", 0)
      id_b = _chunk_id("src/b.py", "func", 0)
      assert id_a != id_b


  def test_chunk_id_is_valid_uuid() -> None:
      cid = _chunk_id("src/foo.py", "bar", 0)
      parsed = uuid.UUID(cid)
      assert parsed.version == 5


  def test_chunk_id_exact_value() -> None:
      """Pin the exact UUID so a future refactor cannot silently change stored IDs."""
      expected = str(uuid.uuid5(uuid.NAMESPACE_URL, "src/foo.py::my_func::0"))
      assert _chunk_id("src/foo.py", "my_func", 0) == expected


  def test_old_start_line_key_no_longer_accepted() -> None:
      """The new signature has 3 positional args; old 2-arg call (path, Chunk) must raise TypeError."""
      from axon.embedder.pipeline import _chunk_id
      with pytest.raises(TypeError):
          _chunk_id("src/foo.py")  # type: ignore[call-arg]
  ```

- [ ] **Step 2:** Run the tests and confirm they FAIL (the current signature is `_chunk_id(path: Path, chunk: Chunk) -> str`).

  ```bash
  pytest tests/embedder/test_pipeline_chunk_id.py -v 2>&1 | head -40
  ```

  Expected output: `FAILED` on `test_chunk_id_stable_across_line_shift`, `test_chunk_id_exact_value`, `test_chunk_id_is_valid_uuid`, and `test_old_start_line_key_no_longer_accepted` (wrong signature). `test_chunk_id_differs_by_occurrence_index` may error with `TypeError`.

- [ ] **Step 3:** Replace `_chunk_id` in `src/axon/embedder/pipeline.py` lines 206-211 with the new implementation.

  Old code (lines 206-211):
  ```python
  def _chunk_id(path: Path, chunk: Chunk) -> str:
      """Stable ID for a chunk: hash of file path + symbol + start_line."""
      import uuid

      key = f"{path}::{chunk.symbol}::{chunk.start_line}"
      return str(uuid.uuid5(uuid.NAMESPACE_URL, key))
  ```

  New code:
  ```python
  def _chunk_id(file_path: str | Path, symbol: str, occurrence_index: int) -> str:
      """Stable chunk ID: does not change when lines above the symbol are edited (D1).

      occurrence_index: 0-based count of times this symbol name has appeared
      within the file, to distinguish overloads and sub-chunks (foo[0], foo[1]).
      """
      import uuid

      key = f"{file_path}::{symbol}::{occurrence_index}"
      return str(uuid.uuid5(uuid.NAMESPACE_URL, key))
  ```

- [ ] **Step 4:** Update the two call sites in `src/axon/embedder/pipeline.py` to pass the new signature.

  In `ingest_file` (around line 109), replace:
  ```python
  id=_chunk_id(path, c),
  ```
  with a version that computes `occurrence_index` per symbol name. Replace the `vector_chunks` list comprehension (lines 107-120) with:
  ```python
  _occ_counter: dict[str, int] = {}
  vector_chunks = []
  for c, vec in zip(chunks, vectors):
      occ = _occ_counter.get(c.symbol, 0)
      _occ_counter[c.symbol] = occ + 1
      vector_chunks.append(
          VectorChunk(
              id=_chunk_id(str(path), c.symbol, occ),
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
  ```

  In `index_path` (around line 171-184), replace the `vector_chunks` list comprehension with:
  ```python
  _occ: dict[str, int] = {}
  vector_chunks = []
  for c, vec in zip(chunks, vectors):
      occ = _occ.get(c.symbol, 0)
      _occ[c.symbol] = occ + 1
      vector_chunks.append(
          VectorChunk(
              id=_chunk_id(str(file_path), c.symbol, occ),
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
  ```

- [ ] **Step 5:** Run the tests and confirm they pass.

  ```bash
  pytest tests/embedder/test_pipeline_chunk_id.py -v
  ```

  Expected: 6 passed.

- [ ] **Step 6:** Run the existing pipeline and chunker tests to check for regressions.

  ```bash
  pytest tests/embedder/test_pipeline_excludes.py tests/embedder/test_chunker_python.py tests/embedder/test_chunker_java.py tests/embedder/test_chunker_typescript.py tests/embedder/test_graph_extractor.py -v
  ```

  Expected: all pass.

- [ ] **Step 7:** Commit.

  ```bash
  git add src/axon/embedder/pipeline.py tests/embedder/test_pipeline_chunk_id.py
  git commit -m "feat(D1): stable chunk-id via uuid5(NAMESPACE_URL, file::symbol::occurrence_index)"
  ```

---

### Task 3: Universal chunk-size cap - Python and TypeScript

**Files:**
- Modify: `src/axon/embedder/chunker.py` lines 326-346 (`_walk_python`), lines 495-512 (`_ts_chunk_from_node`), lines 440-482 (`_walk_ts` to use `extend` instead of `append`)
- Test path: `tests/embedder/test_chunker_size_cap.py` (new file)

**Interfaces:**
- Consumes: `_split_large_node(node, source, symbol, chunk_type, file_path) -> list[Chunk]` at lines 217-241 (existing, unchanged)
- Produces: `_walk_python` emits N chunks for functions exceeding `_MAX_CHUNK_LINES=80`; `_ts_chunk_from_node` returns `list[Chunk]` instead of `Chunk`

- [ ] **Step 1:** Write the failing tests.

  ```python
  # tests/embedder/test_chunker_size_cap.py
  from __future__ import annotations

  from axon.embedder.chunker import _MAX_CHUNK_LINES, chunk_source


  def _make_python_function(name: str, n_lines: int) -> str:
      """Generate a Python function with n_lines body lines."""
      body = "\n".join(f"    x_{i} = {i}" for i in range(n_lines - 1))
      return f"def {name}():\n{body}\n    return x_0\n"


  def _make_ts_function(name: str, n_lines: int) -> str:
      body = "\n".join(f"  const x_{i} = {i};" for i in range(n_lines - 1))
      return f"export function {name}() {{\n{body}\n  return x_0;\n}}\n"


  class TestPythonCap:
      def test_function_exactly_at_cap_is_single_chunk(self) -> None:
          src = _make_python_function("exact", _MAX_CHUNK_LINES)
          chunks = chunk_source(src, "python", "f.py")
          fn_chunks = [c for c in chunks if c.symbol.startswith("exact")]
          assert len(fn_chunks) == 1

      def test_function_below_cap_is_single_chunk(self) -> None:
          src = _make_python_function("small", 10)
          chunks = chunk_source(src, "python", "f.py")
          fn_chunks = [c for c in chunks if c.symbol.startswith("small")]
          assert len(fn_chunks) == 1

      def test_function_above_cap_splits(self) -> None:
          """A 200-line function must produce multiple chunks."""
          src = _make_python_function("big", 200)
          chunks = chunk_source(src, "python", "f.py")
          big_chunks = [c for c in chunks if c.symbol.startswith("big")]
          assert len(big_chunks) > 1, f"expected split, got {len(big_chunks)} chunks"

      def test_split_chunks_none_exceed_cap(self) -> None:
          src = _make_python_function("huge", 400)
          chunks = chunk_source(src, "python", "f.py")
          for c in chunks:
              size = c.end_line - c.start_line + 1
              assert size <= _MAX_CHUNK_LINES, f"chunk {c.symbol} has {size} lines > cap"

      def test_function_79_lines_is_single_chunk(self) -> None:
          src = _make_python_function("under", 79)
          chunks = chunk_source(src, "python", "f.py")
          fn = [c for c in chunks if c.symbol.startswith("under")]
          assert len(fn) == 1

      def test_function_81_lines_splits(self) -> None:
          src = _make_python_function("over", 81)
          chunks = chunk_source(src, "python", "f.py")
          fn = [c for c in chunks if c.symbol.startswith("over")]
          assert len(fn) > 1


  class TestTypeScriptCap:
      def test_ts_function_above_cap_splits(self) -> None:
          src = _make_ts_function("bigTs", 200)
          chunks = chunk_source(src, "typescript", "f.ts")
          big = [c for c in chunks if c.symbol.startswith("bigTs")]
          assert len(big) > 1, f"expected split, got {len(big)}"

      def test_ts_split_none_exceed_cap(self) -> None:
          src = _make_ts_function("hugeTs", 400)
          chunks = chunk_source(src, "typescript", "f.ts")
          for c in chunks:
              size = c.end_line - c.start_line + 1
              assert size <= _MAX_CHUNK_LINES, f"chunk {c.symbol} exceeds cap with {size} lines"

      def test_ts_function_below_cap_is_single_chunk(self) -> None:
          src = _make_ts_function("smallTs", 20)
          chunks = chunk_source(src, "typescript", "f.ts")
          sm = [c for c in chunks if c.symbol.startswith("smallTs")]
          assert len(sm) == 1
  ```

- [ ] **Step 2:** Run the failing tests.

  ```bash
  pytest tests/embedder/test_chunker_size_cap.py -v 2>&1 | head -50
  ```

  Expected: `test_function_above_cap_splits`, `test_split_chunks_none_exceed_cap`, `test_function_81_lines_splits`, `test_ts_function_above_cap_splits`, `test_ts_split_none_exceed_cap` all FAIL (no cap enforced today).

- [ ] **Step 3:** Modify `_walk_python` in `src/axon/embedder/chunker.py` (lines 326-346). Replace the unconditional `chunks.append(...)` inside the `if node.type in ("function_definition",):` branch with a size check:

  Old code (lines 326-338):
  ```python
      if node.type in ("function_definition",):
          symbol = _python_node_identifier(node)
          chunks.append(
              Chunk(
                  symbol=symbol or Path(file_path).stem,
                  chunk_type="method" if in_class else "function",
                  start_line=node.start_point[0] + 1,
                  end_line=node.end_point[0] + 1,
                  content="\n".join(lines[node.start_point[0] : node.end_point[0] + 1]),
                  file_path=file_path,
                  language="python",
              )
          )
  ```

  New code:
  ```python
      if node.type in ("function_definition",):
          symbol = _python_node_identifier(node)
          _sym = symbol or Path(file_path).stem
          _chunk_type: ChunkType = "method" if in_class else "function"
          _start = node.start_point[0] + 1
          _end = node.end_point[0] + 1
          if (_end - _start + 1) > _MAX_CHUNK_LINES:
              # Pass the full-file source bytes so _split_large_node can slice by byte offset.
              chunks.extend(
                  _split_large_node(
                      node,
                      source.encode("utf-8") if isinstance(source, str) else source,
                      _sym,
                      _chunk_type,
                      file_path,
                  )
              )
          else:
              chunks.append(
                  Chunk(
                      symbol=_sym,
                      chunk_type=_chunk_type,
                      start_line=_start,
                      end_line=_end,
                      content="\n".join(lines[node.start_point[0] : node.end_point[0] + 1]),
                      file_path=file_path,
                      language="python",
                  )
              )
  ```

  NOTE: `_chunk_python` at line 286 passes `source: str` to `_walk_python`. `_split_large_node` needs `source: bytes`. The encode call above handles this. Also add `source` parameter to `_walk_python` signature (it already has it - verify at line 311: current signature is `_walk_python(node, source, lines, file_path, *, in_class, chunks)` - `source` is already there).

- [ ] **Step 4:** Modify `_ts_chunk_from_node` (lines 495-512) to return `list[Chunk]` instead of `Chunk`.

  Old code (lines 495-512):
  ```python
  def _ts_chunk_from_node(
      node: Node,
      lines: list[str],
      file_path: str,
      name: str,
      in_class: bool,
  ) -> Chunk:
      start = node.start_point[0]
      end = node.end_point[0]
      return Chunk(
          symbol=name,
          chunk_type="method" if in_class else "function",
          start_line=start + 1,
          end_line=end + 1,
          content="\n".join(lines[start : end + 1]),
          file_path=file_path,
          language="typescript",
      )
  ```

  New code:
  ```python
  def _ts_chunk_from_node(
      node: Node,
      lines: list[str],
      file_path: str,
      name: str,
      in_class: bool,
  ) -> list[Chunk]:
      """Return one or more Chunks for this node, splitting if it exceeds _MAX_CHUNK_LINES."""
      start = node.start_point[0]
      end = node.end_point[0]
      _chunk_type: ChunkType = "method" if in_class else "function"
      if (end - start + 1) > _MAX_CHUNK_LINES:
          # Need source bytes for _split_large_node; reconstruct from lines slice.
          content = "\n".join(lines[start : end + 1])
          source_bytes = content.encode("utf-8")
          # _split_large_node expects a Node with byte offsets into source_bytes.
          # Since we only have lines here, use _split_lines_into_chunks instead.
          return _split_lines_into_chunks(
              lines[start : end + 1],
              start + 1,
              name,
              _chunk_type,
              file_path,
              "typescript",
          )
      return [
          Chunk(
              symbol=name,
              chunk_type=_chunk_type,
              start_line=start + 1,
              end_line=end + 1,
              content="\n".join(lines[start : end + 1]),
              file_path=file_path,
              language="typescript",
          )
      ]
  ```

  NOTE: `_split_lines_into_chunks` is defined in Task 5. Add a forward-reference stub here for now, or implement Tasks 3 and 5 together (recommended). If implementing together, add `_split_lines_into_chunks` now - see Task 5 Step 3 for the exact implementation.

- [ ] **Step 5:** Update `_walk_ts` (lines 440-482) to use `chunks.extend(...)` instead of `chunks.append(...)` since `_ts_chunk_from_node` now returns `list[Chunk]`.

  At line 451:
  Old: `chunks.append(_ts_chunk_from_node(node, lines, file_path, name, in_class))`
  New: `chunks.extend(_ts_chunk_from_node(node, lines, file_path, name, in_class))`

  At line 477:
  Old: `chunks.append(_ts_chunk_from_node(node, lines, file_path, name, in_class))`
  New: `chunks.extend(_ts_chunk_from_node(node, lines, file_path, name, in_class))`

- [ ] **Step 6:** Run the tests.

  ```bash
  pytest tests/embedder/test_chunker_size_cap.py -v
  ```

  Expected: all 9 pass.

- [ ] **Step 7:** Run the existing chunker tests and the recall golden set schema test to confirm no regression.

  ```bash
  pytest tests/embedder/test_chunker_python.py tests/embedder/test_chunker_typescript.py tests/embedder/test_chunker_java.py tests/recall/test_recall_guard.py -v
  ```

  Expected: all pass.

- [ ] **Step 8:** Commit.

  ```bash
  git add src/axon/embedder/chunker.py tests/embedder/test_chunker_size_cap.py
  git commit -m "feat(chunk-cap): enforce 80-line cap in _walk_python and _ts_chunk_from_node"
  ```

---

### Task 4: `_split_lines_into_chunks`, `ChunkType="section"`, Markdown chunker, and text catchall cap

**Files:**
- Modify: `src/axon/embedder/chunker.py` lines 13-15 (`ChunkType`), lines 613-651 (`chunk_source` catchall + markdown branch), add `_split_lines_into_chunks` and `_chunk_markdown` functions
- Test path: `tests/embedder/test_chunker_markdown.py` (new file)

**Interfaces:**
- Consumes: nothing external; `_split_lines_into_chunks` is a pure function on `list[str]`
- Produces:
  - `_split_lines_into_chunks(lines: list[str], start_line_1based: int, symbol: str, chunk_type: ChunkType, file_path: str, language: str) -> list[Chunk]`
  - `_chunk_markdown(source: str, file_path: str) -> list[Chunk]`
  - Updated `ChunkType` including `"section"`
  - Updated `chunk_source` with `language == "markdown"` branch and text catchall using `_split_lines_into_chunks`

- [ ] **Step 1:** Write the failing tests.

  ```python
  # tests/embedder/test_chunker_markdown.py
  from __future__ import annotations

  from axon.embedder.chunker import _MAX_CHUNK_LINES, chunk_source


  class TestSplitLinesIntoChunks:
      def test_200_lines_yields_3_chunks(self) -> None:
          from axon.embedder.chunker import _split_lines_into_chunks
          lines = [f"line {i}" for i in range(200)]
          chunks = _split_lines_into_chunks(lines, 1, "symbol", "function", "f.py", "python")
          assert len(chunks) == 3  # ceil(200/80) = 3 (80+80+40)

      def test_start_and_end_lines_correct(self) -> None:
          from axon.embedder.chunker import _split_lines_into_chunks
          lines = [f"line {i}" for i in range(200)]
          chunks = _split_lines_into_chunks(lines, 1, "sym", "function", "f.py", "python")
          assert chunks[0].start_line == 1
          assert chunks[0].end_line == 80
          assert chunks[1].start_line == 81
          assert chunks[1].end_line == 160
          assert chunks[2].start_line == 161
          assert chunks[2].end_line == 200

      def test_symbol_names_include_index(self) -> None:
          from axon.embedder.chunker import _split_lines_into_chunks
          lines = [f"line {i}" for i in range(200)]
          chunks = _split_lines_into_chunks(lines, 1, "sym", "function", "f.py", "python")
          assert chunks[0].symbol == "sym[0]"
          assert chunks[1].symbol == "sym[1]"
          assert chunks[2].symbol == "sym[2]"

      def test_80_lines_is_single_chunk(self) -> None:
          from axon.embedder.chunker import _split_lines_into_chunks
          lines = [f"line {i}" for i in range(80)]
          chunks = _split_lines_into_chunks(lines, 1, "s", "function", "f.py", "python")
          assert len(chunks) == 1
          assert chunks[0].symbol == "s[0]"


  class TestChunkTypeSection:
      def test_chunk_type_section_valid(self) -> None:
          from axon.embedder.chunker import Chunk
          c = Chunk(
              symbol="intro",
              chunk_type="section",
              start_line=1,
              end_line=5,
              content="# Hello\nworld\n",
              file_path="README.md",
              language="markdown",
          )
          assert c.chunk_type == "section"


  class TestMarkdownChunker:
      def test_3_headers_yield_3_chunks(self) -> None:
          md = "# Intro\nsome text\n## Usage\ncommand\n### Details\nmore\n"
          chunks = chunk_source(md, "markdown", "README.md")
          section_chunks = [c for c in chunks if c.chunk_type == "section"]
          assert len(section_chunks) >= 3

      def test_section_chunk_type(self) -> None:
          md = "# Title\ncontent here\n"
          chunks = chunk_source(md, "markdown", "doc.md")
          assert all(c.chunk_type == "section" for c in chunks)

      def test_large_section_splits(self) -> None:
          body = "\n".join(f"paragraph {i}" for i in range(150))
          md = f"# Big Section\n{body}\n"
          chunks = chunk_source(md, "markdown", "big.md")
          for c in chunks:
              size = c.end_line - c.start_line + 1
              assert size <= _MAX_CHUNK_LINES, f"{c.symbol}: {size} lines"

      def test_no_headers_splits_on_line_cap(self) -> None:
          md = "\n".join(f"line {i}" for i in range(500))
          chunks = chunk_source(md, "markdown", "plain.md")
          assert len(chunks) == 7  # ceil(500/80) = 7 (6x80 + 1x20)
          for c in chunks:
              assert c.end_line - c.start_line + 1 <= _MAX_CHUNK_LINES

      def test_pre_header_content_is_chunked(self) -> None:
          md = "preamble text\n# Section\ncontent\n"
          chunks = chunk_source(md, "markdown", "doc.md")
          assert any(c.start_line == 1 for c in chunks)


  class TestTextCatchall:
      def test_txt_large_file_splits(self) -> None:
          txt = "\n".join(f"line {i}" for i in range(160))
          chunks = chunk_source(txt, "text", "notes.txt")
          assert len(chunks) == 2
          for c in chunks:
              assert c.end_line - c.start_line + 1 <= _MAX_CHUNK_LINES

      def test_unknown_language_splits(self) -> None:
          content = "\n".join(f"row {i}" for i in range(200))
          chunks = chunk_source(content, "unknown_lang", "data.xyz")
          assert len(chunks) >= 3
  ```

- [ ] **Step 2:** Run the failing tests.

  ```bash
  pytest tests/embedder/test_chunker_markdown.py -v 2>&1 | head -60
  ```

  Expected: most tests FAIL (no `_split_lines_into_chunks` exported, `"section"` not in `ChunkType`, no markdown branch).

- [ ] **Step 3:** Add `"section"` to `ChunkType` in `src/axon/embedder/chunker.py` lines 13-15.

  Old:
  ```python
  ChunkType = Literal[
      "method", "constructor", "function", "class", "interface", "enum", "annotation", "record"
  ]
  ```

  New:
  ```python
  ChunkType = Literal[
      "method", "constructor", "function", "class", "interface",
      "enum", "annotation", "record", "section"
  ]
  ```

- [ ] **Step 4:** Add `_split_lines_into_chunks` as a private function after `_split_large_node` (after line 241) in `src/axon/embedder/chunker.py`.

  ```python
  def _split_lines_into_chunks(
      lines: list[str],
      start_line_1based: int,
      symbol: str,
      chunk_type: ChunkType,
      file_path: str,
      language: str,
  ) -> list[Chunk]:
      """Divide a list of text lines into sub-chunks of _MAX_CHUNK_LINES each.

      Used for Markdown sections and plain-text files that have no tree-sitter
      parse tree. Distinct from _split_large_node, which operates on tree-sitter
      Node byte ranges. All sub-chunks (including index 0) are named symbol[idx].
      """
      result: list[Chunk] = []
      for i in range(0, max(len(lines), 1), _MAX_CHUNK_LINES):
          part = lines[i : i + _MAX_CHUNK_LINES]
          idx = i // _MAX_CHUNK_LINES
          result.append(
              Chunk(
                  symbol=f"{symbol}[{idx}]",
                  chunk_type=chunk_type,
                  start_line=start_line_1based + i,
                  end_line=start_line_1based + i + len(part) - 1,
                  content="\n".join(part),
                  file_path=file_path,
                  language=language,
              )
          )
      return result
  ```

- [ ] **Step 5:** Add `_chunk_markdown` as a private function after `_split_lines_into_chunks` in `src/axon/embedder/chunker.py`.

  ```python
  def _chunk_markdown(source: str, file_path: str) -> list[Chunk]:
      """Chunk a Markdown file by heading boundaries.

      Each heading (# through ######) starts a new section. Content before the
      first heading becomes a chunk with symbol = Path(file_path).stem.
      Sections exceeding _MAX_CHUNK_LINES are split via _split_lines_into_chunks.
      A file with no headings is treated as a single section and split on line cap.
      """
      import re
      lines = source.splitlines()
      _HEADING_RE = re.compile(r"^#{1,6}\s+(.+)")

      sections: list[tuple[str, int, list[str]]] = []  # (symbol, start_1based, lines)
      current_symbol = Path(file_path).stem
      current_start = 1
      current_lines: list[str] = []

      for lineno, line in enumerate(lines, start=1):
          m = _HEADING_RE.match(line)
          if m:
              if current_lines:
                  sections.append((current_symbol, current_start, current_lines))
              current_symbol = re.sub(r"[^a-zA-Z0-9_]", "_", m.group(1).strip())[:64]
              current_start = lineno
              current_lines = [line]
          else:
              current_lines.append(line)

      if current_lines:
          sections.append((current_symbol, current_start, current_lines))

      if not sections:
          return [
              Chunk(
                  symbol=Path(file_path).stem,
                  chunk_type="section",
                  start_line=1,
                  end_line=1,
                  content="",
                  file_path=file_path,
                  language="markdown",
              )
          ]

      chunks: list[Chunk] = []
      for symbol, start_1based, sec_lines in sections:
          if len(sec_lines) > _MAX_CHUNK_LINES:
              chunks.extend(
                  _split_lines_into_chunks(sec_lines, start_1based, symbol, "section", file_path, "markdown")
              )
          else:
              chunks.append(
                  Chunk(
                      symbol=symbol,
                      chunk_type="section",
                      start_line=start_1based,
                      end_line=start_1based + len(sec_lines) - 1,
                      content="\n".join(sec_lines),
                      file_path=file_path,
                      language="markdown",
                  )
              )
      return chunks
  ```

- [ ] **Step 6:** Update `chunk_source` in `src/axon/embedder/chunker.py` (lines 613-651). Add a markdown branch and update the catchall.

  Old catchall (lines 639-651):
  ```python
      else:
          lines = source.splitlines()
          return [
              Chunk(
                  symbol=Path(file_path).stem,
                  chunk_type="class",
                  start_line=1,
                  end_line=len(lines),
                  content=source,
                  file_path=file_path,
                  language=language,
              )
          ]
  ```

  New (replace the entire `else` block and add the `elif language == "markdown":` before it):
  ```python
      elif language == "markdown":
          return _chunk_markdown(source, file_path)
      else:
          lines = source.splitlines()
          stem = Path(file_path).stem
          if len(lines) > _MAX_CHUNK_LINES:
              return _split_lines_into_chunks(lines, 1, stem, "section", file_path, language)
          return [
              Chunk(
                  symbol=stem,
                  chunk_type="section",
                  start_line=1,
                  end_line=len(lines) or 1,
                  content=source,
                  file_path=file_path,
                  language=language,
              )
          ]
  ```

  NOTE: insert the `elif language == "markdown":` branch BEFORE the final `else` (currently after `elif language in ("typescript", "ts"):` at line 637).

- [ ] **Step 7:** Run the tests.

  ```bash
  pytest tests/embedder/test_chunker_markdown.py -v
  ```

  Expected: all pass.

- [ ] **Step 8:** Run the full chunker + size-cap suite.

  ```bash
  pytest tests/embedder/test_chunker_python.py tests/embedder/test_chunker_typescript.py tests/embedder/test_chunker_java.py tests/embedder/test_chunker_size_cap.py tests/embedder/test_chunker_markdown.py tests/recall/test_recall_guard.py -v
  ```

  Expected: all pass.

- [ ] **Step 9:** Confirm `_split_lines_into_chunks` is now available for the TypeScript cap (Task 3 Step 4 forward-reference is now resolved). Re-run Task 3 tests.

  ```bash
  pytest tests/embedder/test_chunker_size_cap.py -v
  ```

  Expected: all 9 pass.

- [ ] **Step 10:** Commit.

  ```bash
  git add src/axon/embedder/chunker.py tests/embedder/test_chunker_markdown.py
  git commit -m "feat(chunk-cap): add _split_lines_into_chunks, _chunk_markdown, section ChunkType, text catchall cap"
  ```

---

### Task 5: Parse-once - cache tree-sitter tree in Chunk.metadata["_tree"]

**Files:**
- Modify: `src/axon/embedder/chunker.py` - `_chunk_python` (line 300 tree), `_chunk_typescript` (line 429 tree), `chunk_source` Java branch (line 620 tree), `_split_large_node` (add optional metadata propagation)
- Modify: `src/axon/embedder/graph_extractor.py` - `extract_calls` (lines 79-89), add `_walk_calls_ts_tree`
- Modify: `src/axon/embedder/pipeline.py` - after `build_dependency_records` call (lines 196-202), add `_tree` cleanup loop
- Test path: `tests/embedder/test_parse_once.py` (new file)

**Interfaces:**
- Consumes: `Chunk.metadata: dict` at `chunker.py:48` (`Field(default_factory=dict)`, accepts any value per Pydantic v2 - confirmed); `tree_sitter.Tree` returned by `_PY_PARSER.parse(...)`, `_TS_PARSER.parse(...)`, `_PARSER.parse(...)`
- Produces: `_walk_calls_ts_tree(tree: tree_sitter.Tree) -> list[str]`; `extract_calls` updated to use `chunk.metadata.get("_tree")` for Python/Java/TS with fallback

- [ ] **Step 1:** Write the failing tests.

  ```python
  # tests/embedder/test_parse_once.py
  from __future__ import annotations

  import ast
  from unittest.mock import patch

  import pytest
  import tree_sitter_python as tspython
  from tree_sitter import Language, Parser

  from axon.embedder.chunker import Chunk, _PY_PARSER, chunk_source
  from axon.embedder.graph_extractor import extract_calls


  def _make_py_chunk_with_tree(source: str, symbol: str = "fn") -> Chunk:
      tree = _PY_PARSER.parse(source.encode("utf-8"))
      return Chunk(
          symbol=symbol,
          chunk_type="function",
          start_line=1,
          end_line=len(source.splitlines()),
          content=source,
          file_path="test.py",
          language="python",
          metadata={"_tree": tree},
      )


  class TestWalkCallsTsTree:
      def test_extracts_known_calls(self) -> None:
          from axon.embedder.graph_extractor import _walk_calls_ts_tree
          source = "def fn():\n    foo()\n    bar.baz()\n"
          tree = _PY_PARSER.parse(source.encode("utf-8"))
          calls = _walk_calls_ts_tree(tree)
          assert "foo" in calls

      def test_filters_skip_calls(self) -> None:
          from axon.embedder.graph_extractor import _walk_calls_ts_tree
          source = "def fn():\n    print('hello')\n    len(x)\n    my_func()\n"
          tree = _PY_PARSER.parse(source.encode("utf-8"))
          calls = _walk_calls_ts_tree(tree)
          # "print" and "len" are in _SKIP_CALLS
          assert "print" not in calls
          assert "len" not in calls
          assert "my_func" in calls

      def test_returns_list_not_set(self) -> None:
          from axon.embedder.graph_extractor import _walk_calls_ts_tree
          source = "def fn():\n    foo()\n"
          tree = _PY_PARSER.parse(source.encode("utf-8"))
          result = _walk_calls_ts_tree(tree)
          assert isinstance(result, list)


  class TestExtractCallsUsesCachedTree:
      def test_cached_tree_avoids_ast_parse(self) -> None:
          """If metadata has _tree, ast.parse must NOT be called."""
          source = "def fn():\n    helper()\n"
          chunk = _make_py_chunk_with_tree(source)

          original_parse = ast.parse
          call_count = [0]

          def counting_parse(src: str, *args, **kwargs):  # type: ignore[override]
              call_count[0] += 1
              return original_parse(src, *args, **kwargs)

          with patch("ast.parse", side_effect=counting_parse):
              calls = extract_calls(chunk)

          assert call_count[0] == 0, "ast.parse called despite cached tree"
          assert "helper" in calls

      def test_no_tree_falls_back_to_ast(self) -> None:
          """Without _tree in metadata, ast.parse must be called as fallback."""
          source = "def fn():\n    helper()\n"
          chunk = Chunk(
              symbol="fn",
              chunk_type="function",
              start_line=1,
              end_line=2,
              content=source,
              file_path="test.py",
              language="python",
              metadata={},
          )
          # Should not raise; falls back to ast.parse
          calls = extract_calls(chunk)
          assert "helper" in calls


  class TestChunkerTreeInMetadata:
      def test_python_chunk_has_tree_in_metadata(self) -> None:
          source = "def foo():\n    pass\n"
          chunks = chunk_source(source, "python", "foo.py")
          for c in chunks:
              assert "_tree" in c.metadata, f"chunk {c.symbol} missing _tree"

      def test_typescript_chunk_has_tree_in_metadata(self) -> None:
          source = "export function bar() {\n  return 1;\n}\n"
          chunks = chunk_source(source, "typescript", "bar.ts")
          for c in chunks:
              assert "_tree" in c.metadata, f"chunk {c.symbol} missing _tree"

      def test_java_chunk_has_tree_in_metadata(self) -> None:
          source = "public class Foo {\n  public void go() {}\n}\n"
          chunks = chunk_source(source, "java", "Foo.java")
          for c in chunks:
              assert "_tree" in c.metadata, f"chunk {c.symbol} missing _tree"

      def test_tree_is_not_included_in_qdrant_payload(self) -> None:
          """Confirm that VectorChunk construction (pipeline) does not spread metadata."""
          # The pipeline constructs VectorChunk with explicit fields - no **chunk.metadata.
          # We verify the Chunk model does not bleed into VectorChunk accidentally.
          from axon.store.vector_store import Chunk as VectorChunk
          # VectorChunk has no metadata field - instantiation without it must succeed.
          vc = VectorChunk(
              id="abc",
              vector=[0.1, 0.2],
              file_path="f.py",
              language="python",
              chunk_type="function",
              symbol="fn",
              project="proj",
              ctx="personal",
              content="def fn(): pass",
          )
          assert not hasattr(vc, "metadata")
  ```

- [ ] **Step 2:** Run failing tests.

  ```bash
  pytest tests/embedder/test_parse_once.py -v 2>&1 | head -60
  ```

  Expected: `test_extracts_known_calls`, `test_filters_skip_calls`, `test_returns_list_not_set` FAIL (`_walk_calls_ts_tree` does not exist); `test_python_chunk_has_tree_in_metadata`, `test_typescript_chunk_has_tree_in_metadata`, `test_java_chunk_has_tree_in_metadata` FAIL (no `_tree` in metadata yet).

- [ ] **Step 3:** Add `_walk_calls_ts_tree` to `src/axon/embedder/graph_extractor.py` after `_extract_python_calls` (after line 105).

  ```python
  def _walk_calls_ts_tree(tree: object) -> list[str]:
      """Extract call names from a tree-sitter Tree for Python source.

      The Python tree-sitter grammar uses 'call' nodes (not 'call_expression').
      The callee is accessed via the 'function' field; for attribute calls like
      obj.method(), the attribute name is extracted from the attribute child.
      """
      from tree_sitter import Node as TsNode
      calls: set[str] = set()

      def _visit(node: TsNode) -> None:
          if node.type == "call":
              fn_node = node.child_by_field_name("function")
              if fn_node is not None:
                  if fn_node.type == "identifier":
                      name = fn_node.text.decode("utf-8", errors="replace")
                      if name and name not in _SKIP_CALLS:
                          calls.add(name)
                  elif fn_node.type == "attribute":
                      attr = fn_node.child_by_field_name("attribute")
                      if attr is None:
                          # fallback: last identifier child
                          for child in fn_node.children:
                              if child.type == "identifier":
                                  attr = child
                      if attr is not None:
                          name = attr.text.decode("utf-8", errors="replace")
                          if name and name not in _SKIP_CALLS:
                              calls.add(name)
          for child in node.children:
              _visit(child)

      _visit(tree.root_node)
      return sorted(calls)
  ```

- [ ] **Step 4:** Update `extract_calls` in `src/axon/embedder/graph_extractor.py` (lines 79-89) to use the cached tree.

  Old (lines 79-89):
  ```python
  def extract_calls(chunk: Chunk) -> list[str]:
      if chunk.language == "python":
          calls = _extract_python_calls(chunk.content)
      elif chunk.language == "java":
          calls = _extract_ts_or_java_calls(chunk.content, _JAVA_CALL_PARSER)
      elif chunk.language in {"typescript", "ts"}:
          parser = _TSX_PARSER if chunk.file_path.endswith(".tsx") else _TS_PARSER
          calls = _extract_ts_or_java_calls(chunk.content, parser)
      else:
          calls = []
      return sorted(call for call in calls if call != chunk.symbol)
  ```

  New:
  ```python
  def extract_calls(chunk: Chunk) -> list[str]:
      cached_tree = chunk.metadata.get("_tree")
      if chunk.language == "python":
          if cached_tree is not None:
              raw_calls = _walk_calls_ts_tree(cached_tree)
          else:
              raw_calls = _extract_python_calls(chunk.content)
      elif chunk.language == "java":
          if cached_tree is not None:
              _java_calls: set[str] = set()
              _walk_calls(cached_tree.root_node, _java_calls)
              raw_calls = sorted(c for c in _java_calls if c and c not in _SKIP_CALLS)
          else:
              raw_calls = _extract_ts_or_java_calls(chunk.content, _JAVA_CALL_PARSER)
      elif chunk.language in {"typescript", "ts"}:
          if cached_tree is not None:
              _ts_calls: set[str] = set()
              _walk_calls(cached_tree.root_node, _ts_calls)
              raw_calls = sorted(c for c in _ts_calls if c and c not in _SKIP_CALLS)
          else:
              parser = _TSX_PARSER if chunk.file_path.endswith(".tsx") else _TS_PARSER
              raw_calls = _extract_ts_or_java_calls(chunk.content, parser)
      else:
          raw_calls = []
      return sorted(call for call in raw_calls if call != chunk.symbol)
  ```

- [ ] **Step 5:** Update `_chunk_python` in `src/axon/embedder/chunker.py` to propagate the tree to each chunk. After line 300 (`tree = _PY_PARSER.parse(source.encode("utf-8"))`), pass `metadata={"_tree": tree}` to `_python_fallback_chunk` and update `_walk_python` to accept and stamp the tree.

  The simplest approach: add a `tree` parameter to `_walk_python` and stamp `metadata` on each created `Chunk`. Add `tree` param to `_walk_python` signature (after `chunks`):

  Old signature (line 311):
  ```python
  def _walk_python(
      node: Node,
      source: str,
      lines: list[str],
      file_path: str,
      *,
      in_class: bool,
      chunks: list[Chunk],
  ) -> None:
  ```

  New signature:
  ```python
  def _walk_python(
      node: Node,
      source: str,
      lines: list[str],
      file_path: str,
      *,
      in_class: bool,
      chunks: list[Chunk],
      tree: object | None = None,
  ) -> None:
  ```

  In each `Chunk(...)` constructor call inside `_walk_python`, add `metadata={"_tree": tree} if tree is not None else {}` (or always `metadata={"_tree": tree}` since `tree` defaults to `None`).

  In `_chunk_python` (line 305), update the call:
  ```python
  _walk_python(tree.root_node, source, lines, file_path, in_class=False, chunks=chunks, tree=tree)
  ```

  Also update `_python_fallback_chunk` call in `_chunk_python` to include the tree in metadata. Since `_python_fallback_chunk` is a separate function, either inline the metadata there or change the fallback chunk creation inline:
  ```python
  # In _chunk_python, lines 306-307:
  if not chunks:
      fb = _python_fallback_chunk(source, lines, file_path)
      fb = fb.model_copy(update={"metadata": {"_tree": tree}})
      chunks.append(fb)
  return chunks
  ```

  Update recursive `_walk_python` calls inside `_walk_python` to pass `tree=tree` through.

- [ ] **Step 6:** Update `_chunk_typescript` (line 417-437) to propagate the tree. After `tree = parser.parse(source.encode("utf-8"))` (line 429), pass it to `_walk_ts`. Add `tree` param to `_walk_ts` and stamp on each chunk:

  Update `_ts_chunk_from_node` to accept optional `tree` and set `metadata`:
  ```python
  def _ts_chunk_from_node(
      node: Node,
      lines: list[str],
      file_path: str,
      name: str,
      in_class: bool,
      *,
      tree: object | None = None,
  ) -> list[Chunk]:
  ```
  And in the single-chunk return path: `metadata={"_tree": tree} if tree is not None else {}`

  For `_split_lines_into_chunks` return path in TypeScript, the caller (in `_ts_chunk_from_node`) should set `_tree` on each sub-chunk after the call:
  ```python
  sub_chunks = _split_lines_into_chunks(...)
  if tree is not None:
      for sc in sub_chunks:
          sc.metadata["_tree"] = tree
  return sub_chunks
  ```

  Pass `tree=tree` through `_walk_ts` to `_ts_chunk_from_node`. Update `_walk_ts` signature to accept `tree`.

- [ ] **Step 7:** Update `chunk_source` Java branch in `src/axon/embedder/chunker.py` (around line 620-633) to stamp `_tree` on each returned chunk after `_extract_chunks`.

  After `chunks = _extract_chunks(tree.root_node, source_bytes, file_path)`, add:
  ```python
  for _c in chunks:
      _c.metadata["_tree"] = tree
  ```
  Same for the fallback chunk inside the Java branch.

- [ ] **Step 8:** Update `src/axon/embedder/pipeline.py` after `build_dependency_records` call to clean up `_tree`. After line 202 (after the `upsert_deps` loop), add:

  ```python
  # Clean up non-serializable tree-sitter trees before any parallel phase (Spec C handoff).
  # NOT thread-safe: must complete before any parallel step accesses graph_chunks.
  for _chunk in graph_chunks:
      _chunk.metadata.pop("_tree", None)
  ```

- [ ] **Step 9:** Run the tests.

  ```bash
  pytest tests/embedder/test_parse_once.py -v
  ```

  Expected: all pass.

- [ ] **Step 10:** Run the full embedder suite.

  ```bash
  pytest tests/embedder/ -v
  ```

  Expected: all pass.

- [ ] **Step 11:** Commit.

  ```bash
  git add src/axon/embedder/chunker.py src/axon/embedder/graph_extractor.py src/axon/embedder/pipeline.py tests/embedder/test_parse_once.py
  git commit -m "feat(parse-once): cache tree-sitter tree in Chunk.metadata[_tree], reuse in extract_calls"
  ```

---

### Task 6: `iter_git_files` - security-correct file walk

**Files:**
- Create: `src/axon/repo/__init__.py` (empty)
- Create: `src/axon/repo/file_walk.py`
- Modify: `src/axon/embedder/pipeline.py` lines 59-75 (`iter_supported_files`)
- Modify: `src/axon/code/indexer.py` lines 71-89 (`_iter_repo_files`)
- Create: `tests/test_file_walk_security.py`
- Test path: `tests/test_file_walk_security.py`, `tests/embedder/test_file_walk.py` (new)

**Interfaces:**
- Produces: `iter_git_files(root: Path, *, suffixes: set[str]) -> list[Path]` - tracked files only, gitignored excluded; fallback to rglob when not in a git repo
- Consumes: `subprocess.run(["git", "-C", str(root), "ls-files", "--cached"])`, `subprocess.run(["git", "-C", str(root), "check-ignore", "--stdin", "-z"])`; `EXCLUDED_DIR_NAMES` from `pipeline.py` for rglob fallback

- [ ] **Step 1:** Write the security gate tests (verbatim from spec).

  ```python
  # tests/test_file_walk_security.py
  from __future__ import annotations

  import subprocess
  from pathlib import Path

  import pytest


  @pytest.fixture
  def git_repo(tmp_path: Path) -> Path:
      """Minimal git repo with identity configured."""
      repo = tmp_path / "repo"
      repo.mkdir()
      subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
      subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@test.com"], check=True, capture_output=True)
      subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True, capture_output=True)
      return repo


  def test_gitignored_files_never_embedded(git_repo: Path) -> None:
      """Gitignored files are not returned even if their suffix matches."""
      (git_repo / ".gitignore").write_text(".env\nsecrets.json\n")
      (git_repo / ".env").write_text("SECRET_KEY=abc123\n")
      (git_repo / "secrets.json").write_text('{"password": "hunter2"}\n')
      (git_repo / "main.py").write_text("def hello(): pass\n")
      subprocess.run(["git", "-C", str(git_repo), "add", ".gitignore", "main.py"], check=True, capture_output=True)

      from axon.repo.file_walk import iter_git_files
      files = iter_git_files(git_repo, suffixes={".py", ".env", ".json"})

      names = {f.name for f in files}
      assert ".env" not in names, ".env must not be returned (gitignored)"
      assert "secrets.json" not in names, "secrets.json must not be returned (gitignored)"
      assert "main.py" in names, "main.py must be returned (tracked, not gitignored)"


  def test_committed_then_gitignored_never_embedded(git_repo: Path) -> None:
      """.env committed before .gitignore exists - must be excluded after gitignore added."""
      # First commit: .env without gitignore
      (git_repo / ".env").write_text("SECRET_KEY=abc123\n")
      (git_repo / "main.py").write_text("def hello(): pass\n")
      subprocess.run(["git", "-C", str(git_repo), "add", ".env", "main.py"], check=True, capture_output=True)
      subprocess.run(["git", "-C", str(git_repo), "commit", "-m", "initial"], check=True, capture_output=True)

      # Second commit: add .gitignore that excludes .env
      (git_repo / ".gitignore").write_text(".env\n")
      subprocess.run(["git", "-C", str(git_repo), "add", ".gitignore"], check=True, capture_output=True)
      subprocess.run(["git", "-C", str(git_repo), "commit", "-m", "gitignore"], check=True, capture_output=True)

      from axon.repo.file_walk import iter_git_files
      files = iter_git_files(git_repo, suffixes={".py", ".env"})

      names = {f.name for f in files}
      assert ".env" not in names, ".env is in git ls-files --cached but gitignored post-commit"
      assert "main.py" in names


  def test_untracked_files_not_returned(git_repo: Path) -> None:
      """Untracked files (never git added) must not be returned."""
      (git_repo / "main.py").write_text("def hello(): pass\n")
      subprocess.run(["git", "-C", str(git_repo), "add", "main.py"], check=True, capture_output=True)
      (git_repo / "untracked.py").write_text("def secret(): pass\n")
      # Do NOT add untracked.py

      from axon.repo.file_walk import iter_git_files
      files = iter_git_files(git_repo, suffixes={".py"})

      names = {f.name for f in files}
      assert "main.py" in names
      assert "untracked.py" not in names


  def test_iter_git_files_fallback_outside_git_repo(tmp_path: Path) -> None:
      """Non-git directory falls back to rglob; no crash, returns .py files."""
      (tmp_path / "hello.py").write_text("def hi(): pass\n")
      (tmp_path / "notes.md").write_text("# notes\n")

      from axon.repo.file_walk import iter_git_files
      files = iter_git_files(tmp_path, suffixes={".py"})

      names = {f.name for f in files}
      assert "hello.py" in names
      assert "notes.md" not in names
  ```

- [ ] **Step 2:** Write additional unit tests.

  ```python
  # tests/embedder/test_file_walk.py
  from __future__ import annotations

  import subprocess
  from pathlib import Path

  import pytest


  def test_iter_git_files_returns_only_matching_suffixes(tmp_path: Path) -> None:
      import subprocess
      repo = tmp_path / "r"
      repo.mkdir()
      subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
      subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.com"], check=True, capture_output=True)
      subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True, capture_output=True)
      (repo / "a.py").write_text("pass\n")
      (repo / "b.ts").write_text("const x = 1;\n")
      (repo / "c.md").write_text("# doc\n")
      subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)

      from axon.repo.file_walk import iter_git_files
      files = iter_git_files(repo, suffixes={".py"})
      names = {f.name for f in files}
      assert "a.py" in names
      assert "b.ts" not in names
      assert "c.md" not in names
  ```

- [ ] **Step 3:** Run failing tests.

  ```bash
  pytest tests/test_file_walk_security.py tests/embedder/test_file_walk.py -v 2>&1 | head -30
  ```

  Expected: all FAIL with `ModuleNotFoundError: No module named 'axon.repo'`.

- [ ] **Step 4:** Create `src/axon/repo/__init__.py`.

  ```python
  # src/axon/repo/__init__.py
  ```

- [ ] **Step 5:** Create `src/axon/repo/file_walk.py`.

  ```python
  # src/axon/repo/file_walk.py
  from __future__ import annotations

  import subprocess
  from pathlib import Path

  from axon.embedder.pipeline import EXCLUDED_DIR_NAMES


  def iter_git_files(root: Path, *, suffixes: set[str]) -> list[Path]:
      """List tracked source files respecting .gitignore (D3 security fix).

      Uses 'git ls-files --cached' to list only committed files.
      Applies 'git check-ignore' to exclude files committed before a matching
      .gitignore rule was added. Untracked files require 'git add' first.

      SECURITY GUARANTEE: no gitignored file is returned.

      Fallback: when 'git' is unavailable or root is not a git repo, uses
      rglob with EXCLUDED_DIR_NAMES filtering. The fallback does NOT guarantee
      exclusion of gitignored files - callers outside git repos must accept
      this limitation.
      """
      try:
          result = subprocess.run(
              ["git", "-C", str(root), "ls-files", "--cached"],
              capture_output=True,
              text=True,
              check=True,
          )
      except (subprocess.CalledProcessError, FileNotFoundError):
          return _fallback_rglob(root, suffixes)

      all_tracked = [
          root / line
          for line in result.stdout.splitlines()
          if line and Path(line).suffix in suffixes
      ]
      if not all_tracked:
          return []

      # Apply git check-ignore to filter committed-but-now-gitignored files.
      try:
          check_input = "\n".join(str(p.relative_to(root)) for p in all_tracked)
          ignore_result = subprocess.run(
              ["git", "-C", str(root), "check-ignore", "--stdin"],
              input=check_input,
              capture_output=True,
              text=True,
          )
          ignored_names: set[str] = set(ignore_result.stdout.splitlines())
      except (subprocess.CalledProcessError, FileNotFoundError):
          ignored_names = set()

      return [
          p for p in all_tracked
          if str(p.relative_to(root)) not in ignored_names and p.is_file()
      ]


  def _fallback_rglob(root: Path, suffixes: set[str]) -> list[Path]:
      """Rglob fallback for non-git directories. Does not exclude gitignored files."""
      result: list[Path] = []
      for path in root.rglob("*"):
          if any(part in EXCLUDED_DIR_NAMES for part in path.parts):
              continue
          if path.is_file() and path.suffix in suffixes:
              result.append(path)
      return result
  ```

- [ ] **Step 6:** Run the tests.

  ```bash
  pytest tests/test_file_walk_security.py tests/embedder/test_file_walk.py -v
  ```

  Expected: all pass.

- [ ] **Step 7:** Update `iter_supported_files` in `src/axon/embedder/pipeline.py` (lines 59-75) to call `iter_git_files`.

  Old (lines 59-75):
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

  New:
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

      from axon.repo.file_walk import iter_git_files
      suffixes = {
          s for s, lang in _LANGUAGE_MAP.items()
          if languages is None or lang in languages
      }
      yield from iter_git_files(target, suffixes=suffixes)
  ```

- [ ] **Step 8:** Update `_iter_repo_files` in `src/axon/code/indexer.py` (lines 71-89) to use `iter_git_files`.

  Old (lines 71-89):
  ```python
  def _iter_repo_files(root: Path) -> list[Path]:
      try:
          result = subprocess.run(
              ["git", "-C", str(root), "ls-files", "--cached", "--others",
               "--exclude-standard"],
              capture_output=True,
              text=True,
              check=True,
          )
      except (subprocess.CalledProcessError, FileNotFoundError):
          return list(iter_supported_files(root, languages={"python", "java"}))

      files = [root / line for line in result.stdout.splitlines() if line]
      return [f for f in files if f.suffix in _INDEXED_LANGUAGES and f.is_file()]
  ```

  New:
  ```python
  def _iter_repo_files(root: Path) -> list[Path]:
      """List indexable files under root, respecting .gitignore via iter_git_files (D3)."""
      from axon.repo.file_walk import iter_git_files
      return iter_git_files(root, suffixes=set(_INDEXED_LANGUAGES.keys()))
  ```

  Remove the now-unused `import subprocess` at line 2 if `subprocess` is no longer used elsewhere in `indexer.py` - check first.

- [ ] **Step 9:** Run pipeline and indexer tests.

  ```bash
  pytest tests/embedder/test_pipeline_excludes.py tests/code/test_indexer.py tests/test_file_walk_security.py tests/embedder/test_file_walk.py -v
  ```

  Expected: all pass. Note: `test_pipeline_excludes.py` tests may need updating if they rely on `rglob`-based behavior for non-git directories (the fallback still handles that case).

- [ ] **Step 10:** Run the full recall schema gate.

  ```bash
  pytest tests/recall/test_recall_guard.py -v
  ```

  Expected: all pass.

- [ ] **Step 11:** Commit.

  ```bash
  git add src/axon/repo/__init__.py src/axon/repo/file_walk.py src/axon/embedder/pipeline.py src/axon/code/indexer.py tests/test_file_walk_security.py tests/embedder/test_file_walk.py
  git commit -m "feat(security/D3): iter_git_files - git ls-files --cached + check-ignore, no gitignored files embedded"
  ```

---

### Task 7: Coverage gate and full integration check

**Files:**
- Modify: `pyproject.toml` - add per-module `--cov-fail-under=80` configuration for touched modules
- Test path: all touched test files

**Interfaces:**
- Consumes: all modified modules
- Produces: coverage >= 80% for `axon.embedder.chunker`, `axon.embedder.graph_extractor`, `axon.repo.file_walk`, `axon.embedder.pipeline`

- [ ] **Step 1:** Run coverage check for all changed modules.

  ```bash
  pytest tests/embedder/test_chunker_python.py tests/embedder/test_chunker_java.py tests/embedder/test_chunker_typescript.py tests/embedder/test_chunker_size_cap.py tests/embedder/test_chunker_markdown.py tests/embedder/test_graph_extractor.py tests/embedder/test_graph_extractor_ts_java.py tests/embedder/test_parse_once.py tests/embedder/test_pipeline_chunk_id.py tests/embedder/test_pipeline_excludes.py tests/embedder/test_file_walk.py tests/test_file_walk_security.py tests/code/test_indexer.py --cov=axon.embedder.chunker --cov=axon.embedder.graph_extractor --cov=axon.repo.file_walk --cov=axon.embedder.pipeline --cov-report=term-missing -v
  ```

  Expected: coverage >= 80% for each module. If any module is below 80%, identify uncovered lines in the report and add targeted tests.

- [ ] **Step 2:** If coverage is below 80% for any module, identify the missing lines from the coverage report and add the minimum tests required. For example, if `_chunk_markdown` pre-header path is uncovered:

  ```python
  # In tests/embedder/test_chunker_markdown.py - add to TestMarkdownChunker
  def test_markdown_no_content_empty_file(self) -> None:
      chunks = chunk_source("", "markdown", "empty.md")
      assert len(chunks) >= 1
  ```

- [ ] **Step 3:** Update `pyproject.toml` `[tool.pytest.ini_options]` to record the modules and coverage threshold. Do NOT set `--cov-fail-under` globally (avoids regressing coverage on untouched modules).

  ```toml
  [tool.pytest.ini_options]
  asyncio_mode = "auto"
  asyncio_default_fixture_loop_scope = "function"
  # Per-module coverage for Plan A touched files. Run with:
  # pytest <test files> --cov=axon.embedder.chunker --cov=axon.embedder.graph_extractor --cov=axon.repo.file_walk --cov=axon.embedder.pipeline --cov-fail-under=80
  ```

- [ ] **Step 4:** Run the full test suite to check for regressions across the whole project.

  ```bash
  pytest tests/ -x --ignore=tests/recall/test_recall_guard.py -q 2>&1 | tail -30
  ```

  Expected: all pass except the skipped recall guard test.

- [ ] **Step 5:** Commit.

  ```bash
  git add pyproject.toml
  git commit -m "chore: document per-module coverage gate for Plan A touched files"
  ```

---

### Task 8: Activate the recall guard test

**Files:**
- Modify: `tests/recall/test_recall_guard.py` - remove `@pytest.mark.skip`, add real harness using testcontainers
- Modify: `tests/recall/baseline.json` - populate with actual values after first run
- Test path: `tests/recall/test_recall_guard.py`

NOTE: This task requires testcontainers[qdrant] (already in dev extras). It does NOT run the ONNX embedding model. Instead, it uses a mock embedder that returns deterministic vectors so the test is fast and does not require GPU or model files. The purpose is to confirm the harness infrastructure works and that the golden set schema gates correctly. Real semantic recall is validated manually using the `benchmarks/phase0_baseline.json` workflow.

- [ ] **Step 1:** Update `tests/recall/test_recall_guard.py` to add the infrastructure test with a mock embedder.

  ```python
  from __future__ import annotations

  import json
  from pathlib import Path
  from unittest.mock import MagicMock

  import pytest

  GOLDEN_SET_PATH = Path(__file__).parent / "golden_set.json"
  BASELINE_PATH = Path(__file__).parent / "baseline.json"

  REQUIRED_QUERY_KEYS = {"id", "query", "expected_file", "expected_symbol", "min_score"}


  def test_golden_set_schema() -> None:
      assert GOLDEN_SET_PATH.exists()
      data = json.loads(GOLDEN_SET_PATH.read_text(encoding="utf-8"))
      assert len(data) == 20
      for entry in data:
          missing = REQUIRED_QUERY_KEYS - set(entry.keys())
          assert not missing, f"entry {entry.get('id')} missing keys: {missing}"
          assert isinstance(entry["min_score"], float)
          assert 0.0 < entry["min_score"] <= 1.0


  def test_golden_set_no_duplicate_ids() -> None:
      data = json.loads(GOLDEN_SET_PATH.read_text(encoding="utf-8"))
      ids = [e["id"] for e in data]
      assert len(ids) == len(set(ids))


  def test_baseline_json_exists() -> None:
      assert BASELINE_PATH.exists()
      data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
      assert "recall_top1" in data
      assert "recall_top3" in data
      assert "results_by_query" in data


  def test_recall_guard_harness_infrastructure() -> None:
      """Verify that the recall harness can load the golden set and run checks.

      Uses mock vectors (no embedding model loaded) so this test is fast and
      offline. Real semantic recall is validated by the manual baseline workflow
      documented in benchmarks/phase0_baseline.json.
      """
      from axon.benchmark.contracts import BenchmarkCheck, BenchmarkResult, BenchmarkRunSummary

      golden_set = json.loads(GOLDEN_SET_PATH.read_text(encoding="utf-8"))
      assert len(golden_set) == 20

      # Simulate a harness run where all top-1 hits are wrong (worst case).
      results = []
      for entry in golden_set:
          check = BenchmarkCheck(
              name="top_1_file_match",
              passed=False,
              expected=entry["expected_file"],
              actual="wrong_file.py",
          )
          results.append(
              BenchmarkResult(
                  suite="recall",
                  name=entry["id"],
                  duration_ms=0.0,
                  checks=(check,),
              )
          )

      summary = BenchmarkRunSummary(results=tuple(results))
      assert summary.total == 20
      assert summary.failed == 20
      assert summary.score == 0.0

      # Simulate perfect recall
      perfect_results = []
      for entry in golden_set:
          check = BenchmarkCheck(
              name="top_1_file_match",
              passed=True,
              expected=entry["expected_file"],
              actual=entry["expected_file"],
          )
          perfect_results.append(
              BenchmarkResult(
                  suite="recall",
                  name=entry["id"],
                  duration_ms=0.0,
                  checks=(check,),
              )
          )

      perfect_summary = BenchmarkRunSummary(results=tuple(perfect_results))
      assert perfect_summary.score == 1.0
      assert perfect_summary.passed == 20


  @pytest.mark.skip(
      reason="Requires ONNX embedding model + Qdrant testcontainer. Run manually: "
             "pytest tests/recall/test_recall_guard.py::test_recall_guard_no_regression "
             "--run-slow -s"
  )
  def test_recall_guard_no_regression() -> None:
      """Full semantic recall gate. Skipped in CI unless --run-slow flag is passed.

      To run manually:
          pytest tests/recall/ -k no_regression -s

      Expected: regressions == [] and score >= 0.90 (or no regression vs baseline).
      """
      ...
  ```

- [ ] **Step 2:** Run the updated tests.

  ```bash
  pytest tests/recall/test_recall_guard.py -v
  ```

  Expected: `test_golden_set_schema`, `test_golden_set_no_duplicate_ids`, `test_baseline_json_exists`, `test_recall_guard_harness_infrastructure` pass; `test_recall_guard_no_regression` skipped.

- [ ] **Step 3:** Update `tests/recall/baseline.json` with the structure from a mock-zero-baseline (placeholder until real embedding run).

  ```json
  {
    "_note": "Placeholder. Populate by running: python scripts/populate_recall_baseline.py after GPU is available. Real semantic recall validated in benchmarks/phase0_baseline.json.",
    "recall_top1": null,
    "recall_top3": null,
    "results_by_query": {}
  }
  ```

- [ ] **Step 4:** Commit.

  ```bash
  git add tests/recall/test_recall_guard.py tests/recall/baseline.json
  git commit -m "feat(recall-guard): activate harness infrastructure test, keep semantic gate as manual-only"
  ```

---

### Task 9: Final integration and security smoke test

**Files:**
- No new files - run existing test suite

**Interfaces:**
- Consumes: all modified files
- Produces: clean test run, no regressions

- [ ] **Step 1:** Run the complete security gate suite.

  ```bash
  pytest tests/test_file_walk_security.py -v
  ```

  Expected: all 4 tests pass (gitignored never embedded, committed-then-gitignored excluded, untracked excluded, fallback works).

- [ ] **Step 2:** Run all embedder tests.

  ```bash
  pytest tests/embedder/ -v
  ```

  Expected: all pass.

- [ ] **Step 3:** Run the code indexer tests.

  ```bash
  pytest tests/code/test_indexer.py -v
  ```

  Expected: all pass.

- [ ] **Step 4:** Run the recall schema gate.

  ```bash
  pytest tests/recall/ -v
  ```

  Expected: schema tests pass, semantic guard skipped.

- [ ] **Step 5:** Run the full test suite (excluding slow/embedding tests) for final regression check.

  ```bash
  pytest tests/ -q --tb=short 2>&1 | tail -30
  ```

  Expected: no new failures.

- [ ] **Step 6:** Verify the `_chunk_id` signature is consistent at both call sites in `pipeline.py` using grep.

  ```bash
  grep -n "_chunk_id" src/axon/embedder/pipeline.py
  ```

  Expected output shows 3 lines: the function definition and 2 call sites (in `ingest_file` and `index_path`), all using `(str(path), c.symbol, occ)` form.

- [ ] **Step 7:** Verify no `_tree` leaks to Qdrant payload by checking the VectorChunk construction pattern.

  ```bash
  grep -n "VectorChunk\|chunk\.metadata\|\*\*chunk" src/axon/embedder/pipeline.py
  ```

  Expected: no `**chunk.metadata` in any `VectorChunk(...)` call.

- [ ] **Step 8:** Create a final summary commit tagging Plan A as complete.

  ```bash
  git add .
  git commit -m "chore(plan-A): Plan A complete - chunk-cap, parse-once, git-walk, stable IDs"
  ```

---

## Post-merge checklist (manual, do not run automatically)

- [ ] After GPU embedding is verified working (Plan B complete): run the full semantic recall guard: `pytest tests/recall/test_recall_guard.py::test_recall_guard_no_regression -s`
- [ ] Update `tests/recall/baseline.json` with the real `recall_top1` / `recall_top3` values from the first embedding run
- [ ] Run `benchmarks/phase0_baseline.json` workflow to record `recall_post_split` metric after the chunk-cap is live
- [ ] One-shot migration of the 9 repos (blue/green as described in spec section "Migracao dos 9 repos"): create `_v2` collections, reindex, recall gate, alias swap
- [ ] Verify point count in `personal` collection after migration - expect increase due to split of large functions
- [ ] Confirm `get_providers()` on the embedding model does NOT fall back silently to CPU (from `benchmarks/phase0_baseline.json` gotcha)
