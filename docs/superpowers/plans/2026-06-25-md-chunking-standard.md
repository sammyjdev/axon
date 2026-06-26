# MD Chunking Standard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace AXON's heading-per-chunk Markdown chunker with a structure-aware, token-bounded chunker that merges small sections, splits large ones, and prepends a heading-path breadcrumb.

**Architecture:** A new module `src/axon/embedder/md_chunker.py` owns the algorithm (parse → pack → split → emit). `chunker._chunk_markdown` becomes a thin delegate so `chunker.py` stays lean. Token counting moves to a shared `src/axon/embedder/tokens.py` used by both the chunker and the pipeline.

**Tech Stack:** Python 3.11+, Pydantic v2 (`Chunk` model), pytest. No new dependencies.

## Global Constraints

- Token estimate = `max(1, int(len(text) * 0.35))` — copy verbatim, single source in `tokens.py`.
- Token band: `MIN = 128`, `TARGET = 480`, `MAX = 512` (the `bge-base` embedder window is 512 — never exceed).
- Overlap `0.12` (12%) applies **only** when splitting a single section over `MAX`; never at heading boundaries.
- Breadcrumb format: `" > ".join([stem, *heading_path])`, prepended to `content` as `f"{breadcrumb}\n\n{body}"` and used as `symbol`.
- `chunk_type` for every Markdown chunk is `"section"`.
- Markdown tables (consecutive lines starting with `|`) are atomic — never split mid-row.
- A `#` inside a fenced code block (` ``` ` or `~~~`) is not a heading.
- Pydantic `Chunk` model and the `chunk_source` dispatch signature do not change.

---

### Task 1: Shared token estimator

**Files:**
- Create: `src/axon/embedder/tokens.py`
- Modify: `src/axon/embedder/pipeline.py` (replace local `_estimate_tokens`)
- Test: `tests/embedder/test_tokens.py`

**Interfaces:**
- Produces: `estimate_tokens(text: str) -> int`

- [ ] **Step 1: Write the failing test**

```python
# tests/embedder/test_tokens.py
from axon.embedder.tokens import estimate_tokens


def test_estimate_tokens_is_035_per_char():
    assert estimate_tokens("a" * 100) == 35


def test_estimate_tokens_minimum_one():
    assert estimate_tokens("") == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/embedder/test_tokens.py -v`
Expected: FAIL with `ModuleNotFoundError: axon.embedder.tokens`

- [ ] **Step 3: Write minimal implementation**

```python
# src/axon/embedder/tokens.py
"""Single source of token estimation for chunker + pipeline."""

_TOKENS_PER_CHAR = 0.35


def estimate_tokens(text: str) -> int:
    """Estimate token count as 0.35 * len(text). Returns at least 1."""
    return max(1, int(len(text) * _TOKENS_PER_CHAR))
```

- [ ] **Step 4: Point pipeline at the shared util**

In `src/axon/embedder/pipeline.py`, delete the local `def _estimate_tokens` and its `_TOKENS_PER_CHAR`, and add at the top with the other imports:

```python
from axon.embedder.tokens import estimate_tokens as _estimate_tokens
```

(The alias keeps existing call sites `_estimate_tokens(chunk.content)` working unchanged.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/embedder/test_tokens.py tests/embedder/test_batching.py -v`
Expected: PASS (batching still green — same function, shared now)

- [ ] **Step 6: Commit**

```bash
git add src/axon/embedder/tokens.py src/axon/embedder/pipeline.py tests/embedder/test_tokens.py
git commit -m "refactor(embedder): extract estimate_tokens into shared tokens util"
```

---

### Task 2: Parse Markdown into heading-path sections

**Files:**
- Create: `src/axon/embedder/md_chunker.py`
- Test: `tests/embedder/test_md_sections.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `@dataclass(frozen=True) Section` with fields `heading_path: tuple[str, ...]`, `start_line: int` (1-based), `lines: tuple[str, ...]`
  - `parse_sections(source: str) -> list[Section]`

- [ ] **Step 1: Write the failing test**

```python
# tests/embedder/test_md_sections.py
from axon.embedder.md_chunker import parse_sections


def test_nested_headings_build_path():
    src = "# A\nintro\n## B\nbody\n### C\ndeep\n"
    secs = parse_sections(src)
    assert [s.heading_path for s in secs] == [("A",), ("A", "B"), ("A", "B", "C")]


def test_preamble_before_first_heading_is_its_own_section():
    src = "lead text\nmore\n# A\nbody\n"
    secs = parse_sections(src)
    assert secs[0].heading_path == ()
    assert secs[0].lines == ("lead text", "more")


def test_hash_inside_code_fence_is_not_a_heading():
    src = "# A\n```\n# not a heading\n```\ntail\n"
    secs = parse_sections(src)
    assert [s.heading_path for s in secs] == [("A",)]
    assert "# not a heading" in "\n".join(secs[0].lines)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/embedder/test_md_sections.py -v`
Expected: FAIL with `ModuleNotFoundError: axon.embedder.md_chunker`

- [ ] **Step 3: Write minimal implementation**

```python
# src/axon/embedder/md_chunker.py
"""Structure-aware Markdown chunker (see docs/superpowers/specs/2026-06-25-md-chunking-standard-design.md)."""

from __future__ import annotations

import re
from dataclasses import dataclass

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class Section:
    heading_path: tuple[str, ...]  # () for preamble before the first heading
    start_line: int  # 1-based line of the section's first line
    lines: tuple[str, ...]


def parse_sections(source: str) -> list[Section]:
    lines = source.splitlines()
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []  # (level, text)
    cur_path: tuple[str, ...] = ()
    cur_start = 1
    cur_lines: list[str] = []
    in_fence = False

    def flush() -> None:
        if cur_lines:
            sections.append(Section(cur_path, cur_start, tuple(cur_lines)))

    for lineno, line in enumerate(lines, start=1):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            cur_lines.append(line)
            continue
        m = None if in_fence else _HEADING_RE.match(line)
        if m:
            flush()
            level = len(m.group(1))
            text = m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, text))
            cur_path = tuple(t for _, t in stack)
            cur_start = lineno
            cur_lines = [line]
        else:
            cur_lines.append(line)

    flush()
    return sections
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/embedder/test_md_sections.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/axon/embedder/md_chunker.py tests/embedder/test_md_sections.py
git commit -m "feat(embedder): parse markdown into heading-path sections (fence-aware)"
```

---

### Task 3: Pack small sibling sections toward TARGET

**Files:**
- Modify: `src/axon/embedder/md_chunker.py`
- Test: `tests/embedder/test_md_packing.py`

**Interfaces:**
- Consumes: `Section`, `parse_sections`
- Produces: `pack_sections(sections: list[Section]) -> list[list[Section]]` — groups of sections to become one chunk each. Never groups across differing `heading_path[0]` (top-level boundary); never lets a group exceed `MAX` tokens unless it is a single section.

- [ ] **Step 1: Write the failing test**

```python
# tests/embedder/test_md_packing.py
from axon.embedder.md_chunker import Section, pack_sections


def _sec(path, body):
    return Section(tuple(path), 1, tuple(body.splitlines()))


def test_small_siblings_merge_into_one_group():
    # two tiny sections under the same top-level heading -> one group
    secs = [_sec(["A"], "## a\nshort"), _sec(["A", "b"], "### b\nalso short")]
    groups = pack_sections(secs)
    assert len(groups) == 1
    assert len(groups[0]) == 2


def test_does_not_merge_across_top_level_boundary():
    secs = [_sec(["A"], "# A\nshort"), _sec(["B"], "# B\nshort")]
    groups = pack_sections(secs)
    assert [len(g) for g in groups] == [1, 1]


def test_oversized_single_section_is_its_own_group():
    big = _sec(["A"], "# A\n" + ("word " * 4000))  # > MAX tokens
    groups = pack_sections([big])
    assert len(groups) == 1 and len(groups[0]) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/embedder/test_md_packing.py -v`
Expected: FAIL with `ImportError: cannot import name 'pack_sections'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/axon/embedder/md_chunker.py`:

```python
from axon.embedder.tokens import estimate_tokens

MIN_TOKENS = 128
TARGET_TOKENS = 480
MAX_TOKENS = 512


def _section_text(sec: Section) -> str:
    return "\n".join(sec.lines)


def _top(sec: Section) -> str | None:
    return sec.heading_path[0] if sec.heading_path else None


def pack_sections(sections: list[Section]) -> list[list[Section]]:
    groups: list[list[Section]] = []
    buf: list[Section] = []
    buf_tokens = 0

    def flush() -> None:
        nonlocal buf, buf_tokens
        if buf:
            groups.append(buf)
            buf, buf_tokens = [], 0

    for sec in sections:
        sec_tokens = estimate_tokens(_section_text(sec))
        if not buf:
            buf, buf_tokens = [sec], sec_tokens
            continue
        same_top = _top(sec) == _top(buf[0])
        if same_top and buf_tokens + sec_tokens <= MAX_TOKENS:
            buf.append(sec)
            buf_tokens += sec_tokens
        else:
            flush()
            buf, buf_tokens = [sec], sec_tokens
    flush()
    return groups
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/embedder/test_md_packing.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/axon/embedder/md_chunker.py tests/embedder/test_md_packing.py
git commit -m "feat(embedder): pack small sibling md sections toward target band"
```

---

### Task 4: Split oversized text into overlapping windows (paragraph → sentence → word), tables atomic

**Files:**
- Modify: `src/axon/embedder/md_chunker.py`
- Test: `tests/embedder/test_md_split.py`

**Interfaces:**
- Consumes: `MAX_TOKENS`, `estimate_tokens`
- Produces: `split_text(text: str) -> list[str]` — returns windows each `<= MAX_TOKENS` (best effort), with ~12% overlap between consecutive windows; a Markdown table block is never split.

- [ ] **Step 1: Write the failing test**

```python
# tests/embedder/test_md_split.py
from axon.embedder.md_chunker import split_text, MAX_TOKENS
from axon.embedder.tokens import estimate_tokens


def test_small_text_is_single_window():
    assert split_text("one short paragraph") == ["one short paragraph"]


def test_large_text_splits_under_max_with_overlap():
    paras = "\n\n".join(f"para {i} " + "word " * 200 for i in range(6))
    windows = split_text(paras)
    assert len(windows) >= 2
    assert all(estimate_tokens(w) <= MAX_TOKENS for w in windows)
    # overlap: the tail of window 0 reappears at the head of window 1
    assert windows[0].split()[-1] in windows[1]


def test_table_block_is_not_split_midrow():
    table = "\n".join(f"| r{i} | v{i} |" for i in range(120))  # one big table
    windows = split_text(table)
    # the table stays whole (atomic) even though it exceeds MAX
    assert len(windows) == 1
    assert windows[0].count("\n") == 119
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/embedder/test_md_split.py -v`
Expected: FAIL with `ImportError: cannot import name 'split_text'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/axon/embedder/md_chunker.py`:

```python
_OVERLAP = 0.12
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def _is_table_block(block: str) -> bool:
    rows = [ln for ln in block.splitlines() if ln.strip()]
    return bool(rows) and all(ln.lstrip().startswith("|") for ln in rows)


def _atoms(text: str) -> list[str]:
    """Break text into the smallest units we are willing to keep whole."""
    atoms: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        if not block.strip():
            continue
        if _is_table_block(block) or estimate_tokens(block) <= MAX_TOKENS:
            atoms.append(block)  # paragraph / table kept whole
            continue
        for sent in _SENTENCE_RE.split(block):  # paragraph too big -> sentences
            if estimate_tokens(sent) <= MAX_TOKENS:
                atoms.append(sent)
            else:
                words = sent.split()  # sentence too big -> word windows
                step = max(1, int(MAX_TOKENS / 0.35 / 6))  # ~chars->words budget
                for i in range(0, len(words), step):
                    atoms.append(" ".join(words[i : i + step]))
    return atoms


def split_text(text: str) -> list[str]:
    if estimate_tokens(text) <= MAX_TOKENS:
        return [text]
    atoms = _atoms(text)
    windows: list[str] = []
    cur: list[str] = []
    for atom in atoms:
        candidate = cur + [atom]
        if cur and estimate_tokens("\n\n".join(candidate)) > MAX_TOKENS:
            windows.append("\n\n".join(cur))
            # overlap: carry the last atom into the next window
            overlap_atom = cur[-1]
            cur = [overlap_atom, atom] if estimate_tokens(overlap_atom) <= MAX_TOKENS * _OVERLAP * 4 else [atom]
        else:
            cur = candidate
    if cur:
        windows.append("\n\n".join(cur))
    return windows or [text]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/embedder/test_md_split.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/axon/embedder/md_chunker.py tests/embedder/test_md_split.py
git commit -m "feat(embedder): overlapping paragraph/sentence/word split, atomic tables"
```

---

### Task 5: Assemble `chunk_markdown` (breadcrumb + emit) and wire into `chunk_source`

**Files:**
- Modify: `src/axon/embedder/md_chunker.py`
- Modify: `src/axon/embedder/chunker.py` (replace body of `_chunk_markdown`)
- Test: `tests/embedder/test_chunker_markdown.py` (extend existing)

**Interfaces:**
- Consumes: `parse_sections`, `pack_sections`, `split_text`, `Chunk`
- Produces: `chunk_markdown(source: str, file_path: str) -> list[Chunk]`

- [ ] **Step 1: Write the failing test**

```python
# tests/embedder/test_chunker_markdown.py  (add these)
from pathlib import Path
from axon.embedder.chunker import chunk_source
from axon.embedder.md_chunker import MIN_TOKENS, MAX_TOKENS
from axon.embedder.tokens import estimate_tokens


def test_breadcrumb_prepended_to_content_and_symbol():
    src = "# Doc\n## Costs\n### Token cost\nbody text here\n"
    chunks = chunk_source(src, "markdown", "/x/ENGINE.md")
    c = chunks[-1]
    assert c.symbol == "ENGINE > Doc > Costs > Token cost"
    assert c.content.startswith("ENGINE > Doc > Costs > Token cost\n\n")


def test_duplicate_headings_become_distinct_chunks():
    src = "# A\n## Token cost\nx\n# B\n## Token cost\ny\n"
    chunks = chunk_source(src, "markdown", "/x/d.md")
    symbols = [c.symbol for c in chunks]
    assert "d > A > Token cost" in symbols
    assert "d > B > Token cost" in symbols


def test_no_chunk_below_min_except_singletons():
    src = "# A\n## a\nshort\n## b\nshort\n## c\nshort\n"
    chunks = chunk_source(src, "markdown", "/x/d.md")
    big = [c for c in chunks if estimate_tokens(c.content) >= MAX_TOKENS]
    assert big == []  # nothing over the cap
    assert len(chunks) >= 1


def test_file_with_no_headings_falls_back():
    chunks = chunk_source("just prose, no headings", "markdown", "/x/note.md")
    assert len(chunks) == 1
    assert chunks[0].symbol == "note"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/embedder/test_chunker_markdown.py -v`
Expected: FAIL (breadcrumb symbol mismatch — old chunker returns sanitized single heading)

- [ ] **Step 3: Write `chunk_markdown` in md_chunker.py**

Append to `src/axon/embedder/md_chunker.py`:

```python
from pathlib import Path


def _breadcrumb(stem: str, heading_path: tuple[str, ...]) -> str:
    return " > ".join([stem, *heading_path])


def chunk_markdown(source: str, file_path: str):
    from axon.embedder.chunker import Chunk  # local import avoids cycle

    stem = Path(file_path).stem
    sections = parse_sections(source)
    if not sections:
        return [
            Chunk(symbol=stem, chunk_type="section", start_line=1, end_line=1,
                  content="", file_path=file_path, language="markdown")
        ]

    chunks: list[Chunk] = []
    for group in pack_sections(sections):
        lead = group[0]
        crumb = _breadcrumb(stem, lead.heading_path)
        body = "\n".join(ln for sec in group for ln in sec.lines)
        start = lead.start_line
        end = group[-1].start_line + len(group[-1].lines) - 1
        windows = split_text(body)
        for i, win in enumerate(windows):
            symbol = crumb if len(windows) == 1 else f"{crumb}[{i}]"
            chunks.append(
                Chunk(symbol=symbol, chunk_type="section",
                      start_line=start, end_line=end,
                      content=f"{crumb}\n\n{win}", file_path=file_path,
                      language="markdown")
            )
    return chunks
```

- [ ] **Step 4: Delegate from chunker.py**

In `src/axon/embedder/chunker.py`, replace the entire body of `def _chunk_markdown(source, file_path)` with a delegate:

```python
def _chunk_markdown(source: str, file_path: str) -> list[Chunk]:
    """Structure-aware markdown chunking. See embedder/md_chunker.py."""
    from axon.embedder.md_chunker import chunk_markdown
    return chunk_markdown(source, file_path)
```

Leave `chunk_source`'s `elif language == "markdown": return _chunk_markdown(...)` branch unchanged.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/embedder/test_chunker_markdown.py tests/embedder/test_md_sections.py tests/embedder/test_md_packing.py tests/embedder/test_md_split.py -v`
Expected: PASS (new + existing markdown tests; update any pre-existing markdown assertions that encoded the old one-chunk-per-heading symbol format)

- [ ] **Step 6: Commit**

```bash
git add src/axon/embedder/md_chunker.py src/axon/embedder/chunker.py tests/embedder/test_chunker_markdown.py
git commit -m "feat(embedder): structure-aware md chunker with heading-path breadcrumb"
```

---

### Task 6: Re-index validation (not a unit test)

**Files:**
- None (operational validation)

- [ ] **Step 1: Re-index the 7 projects into pgvector**

```bash
export AXON_PG_URL="postgresql://axon:axon@localhost:5434/axon"
for p in axon glyph-kg pharos-backend lume gnomon-eval pharos-frontend lina; do
  pb index-dev --project "$p" 2>&1 | grep -iE "file|error"
done
```

- [ ] **Step 2: Compare MD chunk size distribution before/after**

```bash
docker exec axon-axon-postgres-1 psql -U axon -d axon -c \
"SELECT width_bucket(length(content), 0, 4000, 8) AS bucket, count(*) \
 FROM embeddings WHERE language='markdown' GROUP BY 1 ORDER BY 1;"
```

Expected: chunks concentrated in the mid buckets (≈128–512 tokens ≈ 360–1460 chars), few tiny outliers, none above the cap (aside from atomic tables). Record the histogram in the PR description.

- [ ] **Step 3: Spot-check retrieval**

Run a few `search_code` queries that previously hit duplicate "Custo de tokens" sections and confirm the breadcrumb disambiguates them in the results.

---

## Self-Review

**Spec coverage:** heading-stack parse (Task 2), 128/480/512 band + merge (Task 3), paragraph→sentence→word split + overlap-on-split + atomic tables (Task 4), breadcrumb in content+symbol + duplicate disambiguation + no-heading/preamble fallbacks (Tasks 2/5), shared token estimator (Task 1), re-index migration (Task 6). All spec sections map to a task.

**Placeholder scan:** no TBD/TODO; every code step shows complete code.

**Type consistency:** `Section(heading_path, start_line, lines)`, `parse_sections`, `pack_sections`, `split_text`, `chunk_markdown`, `estimate_tokens`, `MIN/TARGET/MAX_TOKENS` used consistently across tasks; `chunk_type="section"` and `language="markdown"` match the existing `Chunk` model.

**Known follow-ups (out of scope):** the `split_text` overlap heuristic is intentionally simple (carry-last-atom); tune the word-window `step` if histograms show drift. Sub-project B (MD generation templates) is a separate plan.
