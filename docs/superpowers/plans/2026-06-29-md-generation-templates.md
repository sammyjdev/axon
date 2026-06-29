# MD Generation Templates Implementation Plan (sub-project B)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make AXON's generated vault Markdown retrieval-friendly — frontmatter facets out of the embedded body, a redundancy-free breadcrumb, and grouped decision lists — and teach the chunker to ignore leading YAML frontmatter.

**Architecture:** Two seams. (1) `embedder/md_chunker.py` strips a leading frontmatter block before parsing headings (benefits every frontmatter-bearing doc in the corpus). (2) `obsidian/exporter.py` renders decision notes as `frontmatter + # {summary}` and Architecture/Summaries as H2-by-status grouped lists, via small private helpers.

**Tech Stack:** Python 3.11+, Pydantic v2 (`Decision` model), PyYAML (already a dependency, used in `core/decision.py`), pytest. No new dependencies.

## Global Constraints

- Frontmatter regex matches only at the very start of the document (`\A---\n…`), non-greedy, `re.DOTALL` — same shape as `core/decision.py:_FRONTMATTER_RE`.
- `_strip_frontmatter` MUST NOT raise on malformed input; it degrades to "no frontmatter" (`(source, 0)`).
- Chunk `start_line`/`end_line` MUST stay correct after stripping: shift by the number of lines the frontmatter consumed.
- Decision-note H1 is `# {summary}` — never `# {id} — {summary}` (the doubled-id breadcrumb is the bug being removed).
- Decision-note frontmatter facets (not embedded): `id, status, repo, agent, timestamp` (ISO), `validation_score`, `git_hash`, `files` (POSIX strings), `symbols`. `tags` and `linked_decisions` live in the BODY, not frontmatter.
- `yaml.safe_dump(..., sort_keys=True, allow_unicode=True)` for all frontmatter (pt-BR content must survive).
- Architecture/Summaries group by `Decision.status` in fixed order `active, draft, superseded, deprecated`; empty groups omitted; entirely-empty doc renders `_None._`.
- All sub-project A invariants (token band 128/480/512, atomic tables, breadcrumb budget) stay intact. Do not weaken existing chunker tests.
- Validation commands prefix with `rtk` (e.g. `rtk pytest tests/ -q`, `rtk ruff check`).

---

### Task 1: Chunker skips leading YAML frontmatter

**Files:**
- Modify: `src/axon/embedder/md_chunker.py` (add `_strip_frontmatter`; wire into `chunk_markdown`)
- Test: `tests/embedder/test_md_frontmatter.py` (create)

**Interfaces:**
- Produces: `_strip_frontmatter(source: str) -> tuple[str, int]` — returns `(body, line_offset)`.
- Modifies: `chunk_markdown(source: str, file_path: str) -> list[Chunk]` — now strips frontmatter first and shifts line numbers by `line_offset`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/embedder/test_md_frontmatter.py
from axon.embedder.chunker import chunk_source
from axon.embedder.md_chunker import _strip_frontmatter


def test_strip_frontmatter_returns_body_and_offset():
    src = "---\na: 1\nb: two\n---\n# Head\nbody\n"
    body, offset = _strip_frontmatter(src)
    assert body == "# Head\nbody\n"
    assert offset == 4  # ---, a, b, --- consumed before the body


def test_no_frontmatter_is_unchanged():
    src = "# Head\nbody\n"
    assert _strip_frontmatter(src) == (src, 0)


def test_unterminated_frontmatter_is_treated_as_body():
    src = "---\na: 1\n# Head\nbody\n"  # no closing ---
    assert _strip_frontmatter(src) == (src, 0)


def test_frontmatter_is_not_embedded_and_line_numbers_shift():
    src = "---\nid: dec-001\nstatus: active\n---\n# My Title\nsome body text\n"
    chunks = chunk_source(src, "markdown", "/x/dec-001.md")
    joined = "\n".join(c.content for c in chunks)
    assert "id: dec-001" not in joined  # frontmatter not embedded
    assert "---" not in joined
    assert "My Title" in joined
    # "# My Title" is line 5 in the original (4 frontmatter lines precede it)
    assert chunks[0].start_line == 5


def test_middocument_thematic_break_is_not_stripped():
    src = "# Head\nbefore\n\n---\n\nafter\n"  # --- is a thematic break, not frontmatter
    body, offset = _strip_frontmatter(src)
    assert offset == 0 and body == src
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `rtk pytest tests/embedder/test_md_frontmatter.py -v`
Expected: FAIL with `ImportError: cannot import name '_strip_frontmatter'`

- [ ] **Step 3: Add `_strip_frontmatter`**

Add near the top of `src/axon/embedder/md_chunker.py`, after the existing `_FENCE_RE` definition:

```python
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)


def _strip_frontmatter(source: str) -> tuple[str, int]:
    """Strip a leading YAML frontmatter block.

    Returns ``(body, line_offset)`` where ``line_offset`` is the number of
    lines the frontmatter block (including its fences) consumed, so chunk line
    numbers can be shifted back onto the original source. A document with no
    frontmatter, or an unterminated ``---`` block, returns ``(source, 0)``.
    """
    match = _FRONTMATTER_RE.match(source)
    if not match:
        return source, 0
    line_offset = source[: match.end()].count("\n")
    return source[match.end() :], line_offset
```

- [ ] **Step 4: Wire it into `chunk_markdown`**

In `chunk_markdown`, replace the line `sections = parse_sections(source)` with a strip-first version and add `line_offset` to the start/end of each emitted chunk. The function becomes:

```python
def chunk_markdown(source: str, file_path: str) -> list[Chunk]:
    from axon.embedder.chunker import Chunk  # local import avoids cycle

    stem = Path(file_path).stem
    body_source, line_offset = _strip_frontmatter(source)
    sections = parse_sections(body_source)
    if not sections:
        return [
            Chunk(symbol=stem, chunk_type="section", start_line=1, end_line=1,
                  content="", file_path=file_path, language="markdown")
        ]

    chunks: list[Chunk] = []
    for group in pack_sections(sections):
        first = group[0]
        crumb = _breadcrumb(stem, _group_heading_path(group))
        body = "\n".join(ln for sec in group for ln in sec.lines)
        start = first.start_line + line_offset
        end = group[-1].start_line + len(group[-1].lines) - 1 + line_offset
        crumb_tokens = estimate_tokens(f"{crumb}\n\n")
        # Subtract 1 extra token to absorb floor-truncation: int(a)+int(b) can be
        # 1 less than int(a+b) when fractional parts sum to >= 1.
        body_budget = max(MIN_TOKENS, MAX_TOKENS - crumb_tokens - 1)
        windows = split_text(body, body_budget)
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

- [ ] **Step 5: Run tests to verify they pass**

Run: `rtk pytest tests/embedder/test_md_frontmatter.py tests/embedder/test_md_sections.py tests/embedder/test_md_packing.py tests/embedder/test_md_split.py tests/embedder/test_chunker_markdown.py -v`
Expected: PASS (new frontmatter tests + all sub-project A markdown tests still green)

- [ ] **Step 6: Lint + commit**

```bash
rtk ruff check src/axon/embedder/md_chunker.py
git add src/axon/embedder/md_chunker.py tests/embedder/test_md_frontmatter.py
git commit -m "feat(embedder): skip leading YAML frontmatter when chunking markdown"
```

---

### Task 2: Decision note as frontmatter + summary prose

**Files:**
- Modify: `src/axon/obsidian/exporter.py` (add `yaml` import + `_render_note`; rewrite `export_adr`)
- Test: `tests/obsidian/test_exporter.py` (extend)

**Interfaces:**
- Consumes: `_atomic_write` (existing), `Decision` (fields `id, status, repo, agent, timestamp, validation_score, git_hash, files, symbols, summary, tags, linked_decisions`).
- Produces: `_render_note(frontmatter: dict[str, object], body: str) -> str`; `export_adr(decision, *, vault) -> Path` (unchanged signature, new output format).

- [ ] **Step 1: Write the failing tests**

Add to `tests/obsidian/test_exporter.py`:

```python
def test_export_adr_uses_frontmatter_and_clean_heading(tmp_path: Path) -> None:
    decision = _decision(
        symbols=["pkg.Mod"], linked_decisions=["dec-002"], tags=["embedder", "chunking"]
    )
    text = export_adr(decision, vault=tmp_path).read_text(encoding="utf-8")
    # frontmatter block with facets, body with clean heading
    assert text.startswith("---\n")
    assert "id: dec-001" in text
    assert "symbols:" in text and "pkg.Mod" in text
    # H1 is the summary, NOT "# dec-001 — ..."
    assert "# drop neo4j backend" in text
    assert "# dec-001" not in text
    # tags as obsidian hashtags + related wikilink in the body
    assert "#embedder" in text and "#chunking" in text
    assert "**Related:** [[dec-002]]" in text


def test_export_adr_chunks_without_redundant_breadcrumb(tmp_path: Path) -> None:
    from axon.embedder.chunker import chunk_source

    path = export_adr(_decision(), vault=tmp_path)
    chunks = chunk_source(path.read_text(encoding="utf-8"), "markdown", str(path))
    symbols = [c.symbol for c in chunks]
    contents = "\n".join(c.content for c in chunks)
    assert "dec-001 > drop neo4j backend" in symbols
    assert "dec-001 > dec-001" not in " ".join(symbols)  # no doubled id
    assert "validation_score" not in contents  # facets not embedded
    assert "---" not in contents
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `rtk pytest tests/obsidian/test_exporter.py -v`
Expected: FAIL (old `export_adr` writes `# dec-001 — …` and bullet metadata, so the new assertions fail)

- [ ] **Step 3: Add the `yaml` import and `_render_note`**

At the top of `src/axon/obsidian/exporter.py`, add to the imports:

```python
import yaml  # type: ignore[import-untyped]
```

Add this helper (after the `_atomic_write` function):

```python
def _render_note(frontmatter: dict[str, object], body: str) -> str:
    """Render a vault note: YAML frontmatter block + markdown body."""
    front = yaml.safe_dump(frontmatter, sort_keys=True, allow_unicode=True).strip()
    return f"---\n{front}\n---\n\n{body}\n"
```

- [ ] **Step 4: Rewrite `export_adr`**

Replace the entire body of `export_adr` with:

```python
def export_adr(decision: Decision, *, vault: Path) -> Path:
    """Write one decision as an ADR note at ``AXON/Decisions/<id>.md``.

    Metadata lives in YAML frontmatter (filter facets, not embedded); the body
    is the summary as an H1 plus optional ``#tags`` and ``[[related]]`` links.
    """
    frontmatter: dict[str, object] = {
        "id": decision.id,
        "status": decision.status,
        "repo": decision.repo,
        "agent": decision.agent,
        "timestamp": decision.timestamp.isoformat(),
        "validation_score": decision.validation_score,
        "git_hash": decision.git_hash or "",
        "files": [f.as_posix() for f in decision.files],
        "symbols": list(decision.symbols),
    }
    body_lines = [f"# {decision.summary}"]
    if decision.tags:
        body_lines += ["", " ".join(f"#{t}" for t in decision.tags)]
    if decision.linked_decisions:
        related = " ".join(f"[[{d}]]" for d in decision.linked_decisions)
        body_lines += ["", f"**Related:** {related}"]
    content = _render_note(frontmatter, "\n".join(body_lines))
    return _atomic_write(vault / _ROOT / _DECISIONS_DIR / f"{decision.id}.md", content)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `rtk pytest tests/obsidian/test_exporter.py -v`
Expected: PASS — the new tests and the pre-existing `test_export_adr_writes_decision_note`, `test_write_is_atomic_and_leaves_no_tmp`, `test_export_does_not_touch_existing_vault_notes` all green (they assert presence of summary/symbol/`[[dec-002]]`, which the new format still satisfies). If any pre-existing assertion encodes the OLD bullet format and fails, update it to the new format — do not weaken it.

- [ ] **Step 6: Lint + commit**

```bash
rtk ruff check src/axon/obsidian/exporter.py
git add src/axon/obsidian/exporter.py tests/obsidian/test_exporter.py
git commit -m "feat(obsidian): decision note as frontmatter + summary prose (no doubled-id breadcrumb)"
```

---

### Task 3: Architecture & Summaries grouped by status

**Files:**
- Modify: `src/axon/obsidian/exporter.py` (add `_grouped_decisions`; rewrite `export_architecture_doc`, `export_project_summary`)
- Test: `tests/obsidian/test_exporter.py` (extend)

**Interfaces:**
- Consumes: `_render_note` (Task 2), `_atomic_write`, `Decision` (`status, id, summary, timestamp`).
- Produces: `_grouped_decisions(decisions: list[Decision]) -> str`; `export_architecture_doc(decisions, *, vault, name="architecture") -> Path`; `export_project_summary(repo, since, decisions, *, vault) -> Path` (signatures unchanged).

- [ ] **Step 1: Write the failing tests**

Add to `tests/obsidian/test_exporter.py`:

```python
def test_architecture_doc_groups_by_status(tmp_path: Path) -> None:
    decisions = [
        _decision(id="dec-001", status="active", summary="keep postgres"),
        _decision(id="dec-002", status="superseded", summary="old qdrant path"),
    ]
    text = export_architecture_doc(decisions, vault=tmp_path).read_text(encoding="utf-8")
    assert text.startswith("---\n")  # frontmatter
    assert "## Active" in text and "## Superseded" in text
    assert "- [[dec-001]] — keep postgres" in text
    assert "- [[dec-002]] — old qdrant path" in text
    # Active group precedes Superseded group
    assert text.index("## Active") < text.index("## Superseded")


def test_architecture_doc_empty_renders_none(tmp_path: Path) -> None:
    text = export_architecture_doc([], vault=tmp_path).read_text(encoding="utf-8")
    assert "_None._" in text


def test_summary_filters_by_date_and_groups(tmp_path: Path) -> None:
    old = _decision(id="dec-001", timestamp=datetime(2026, 1, 1, tzinfo=UTC))
    new = _decision(id="dec-002", status="active", timestamp=datetime(2026, 5, 20, tzinfo=UTC))
    text = export_project_summary(
        "axon", date(2026, 5, 1), [old, new], vault=tmp_path
    ).read_text(encoding="utf-8")
    assert "[[dec-002]]" in text
    assert "[[dec-001]]" not in text
    assert "## Active" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `rtk pytest tests/obsidian/test_exporter.py -k "group or empty_renders" -v`
Expected: FAIL (old docs are flat link lists with no `## Active` heading and no frontmatter)

- [ ] **Step 3: Add `_grouped_decisions` and status constants**

Add to `src/axon/obsidian/exporter.py` (near the other module constants):

```python
_STATUS_ORDER = ("active", "draft", "superseded", "deprecated")
_STATUS_HEADING = {
    "active": "Active",
    "draft": "Draft",
    "superseded": "Superseded",
    "deprecated": "Deprecated",
}


def _grouped_decisions(decisions: list[Decision]) -> str:
    """Body of grouped decision links: an H2 per non-empty status, in fixed
    order, with ``- [[id]] — summary`` entries. Empty input -> ``_None._``."""
    if not decisions:
        return "_None._"
    blocks: list[str] = []
    for status in _STATUS_ORDER:
        group = [d for d in decisions if d.status == status]
        if not group:
            continue
        lines = [f"## {_STATUS_HEADING[status]}", ""]
        lines += [f"- [[{d.id}]] — {d.summary}" for d in group]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) if blocks else "_None._"
```

- [ ] **Step 4: Rewrite the two exporters**

Replace the bodies of `export_architecture_doc` and `export_project_summary` with:

```python
def export_architecture_doc(
    decisions: list[Decision], *, vault: Path, name: str = "architecture"
) -> Path:
    """Write an architecture overview grouping decisions by status."""
    frontmatter: dict[str, object] = {
        "kind": "architecture",
        "name": name,
        "generated": _now(),
    }
    body = f"# Architecture — {name}\n\n{_grouped_decisions(decisions)}"
    return _atomic_write(
        vault / _ROOT / _ARCHITECTURE_DIR / f"{name}.md",
        _render_note(frontmatter, body),
    )


def export_project_summary(
    repo: str, since: date, decisions: list[Decision], *, vault: Path
) -> Path:
    """Write a summary of a repo's decisions made on or after ``since``."""
    recent = [d for d in decisions if d.timestamp.date() >= since]
    frontmatter: dict[str, object] = {
        "kind": "summary",
        "repo": repo,
        "since": since.isoformat(),
        "generated": _now(),
    }
    body = f"# Summary — {repo}\n\n{_grouped_decisions(recent)}"
    return _atomic_write(
        vault / _ROOT / _SUMMARIES_DIR / f"{repo}.md",
        _render_note(frontmatter, body),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `rtk pytest tests/obsidian/test_exporter.py -v`
Expected: PASS — new grouping tests + pre-existing `test_export_architecture_doc_wikilinks_decisions` and `test_export_project_summary_filters_by_date` (both assert presence of `[[id]]`, still satisfied). Update any pre-existing assertion that encodes the OLD flat-list format to the new grouped format — do not weaken it.

- [ ] **Step 6: Lint + commit**

```bash
rtk ruff check src/axon/obsidian/exporter.py
git add src/axon/obsidian/exporter.py tests/obsidian/test_exporter.py
git commit -m "feat(obsidian): group architecture/summary docs by decision status"
```

---

### Task 4: Re-index migration (operational validation)

**Files:**
- None (operational validation)

Two effects need migrating onto the corpus: the chunker frontmatter-skip (Task 1) benefits any frontmatter-bearing doc the moment it is re-chunked; the new exporter output (Tasks 2-3) only appears once the vault docs are regenerated.

- [ ] **Step 1: Regenerate the vault docs with the new templates**

Re-run AXON's export entry point so `AXON/Decisions|Architecture|Summaries` are rewritten in the new format. Confirm the available command first:

```bash
axon export --help   # confirm the export subcommand + flags on this build
axon export          # regenerate vault docs from SessionStore decisions
```

(If the public `axon export` surface differs, use the equivalent path that calls `obsidian.exporter` — `__main__.py` wires `export_adr`/`export_architecture_doc`/`export_project_summary`.)

- [ ] **Step 2: Invalidate the file cache and re-index**

```bash
export AXON_PG_URL="postgresql://axon:axon@localhost:5434/axon"
docker exec axon-axon-postgres-1 psql -U axon -d axon -c \
  "DELETE FROM file_index WHERE file_path LIKE '%.md';"
pb index            # re-chunk the whole vault (frontmatter now stripped)
```

- [ ] **Step 3: Verify the migration**

```bash
docker exec axon-axon-postgres-1 psql -U axon -d axon -c \
 "SELECT left(symbol,60) FROM embeddings WHERE language='markdown' \
  AND file_path ~ 'AXON/Decisions/' ORDER BY random() LIMIT 8;"
```

Expected: Decision symbols read `dec-NNN > <summary>` (no doubled id). Then confirm no embedded frontmatter remains:

```bash
docker exec axon-axon-postgres-1 psql -U axon -d axon -t -c \
 "SELECT count(*) FROM embeddings WHERE language='markdown' AND content LIKE '%validation_score:%';"
```

Expected: `0` (decision metadata no longer embedded). Record the before/after in the execution notes.

---

## Self-Review

**Spec coverage:** C1 frontmatter-skip → Task 1; C2 decision note (frontmatter + `# {summary}` + tags + related) → Task 2; C3 grouped Architecture/Summaries → Task 3; migration → Task 4. Out-of-scope items (`draft_pool`, facet columns, tag sub-grouping) are not tasked, as intended.

**Placeholder scan:** no TBD/TODO; every code step shows complete code. Task 4 is operational and names exact commands (with a documented fallback for the `axon export` surface).

**Type consistency:** `_strip_frontmatter(str) -> tuple[str,int]`, `_render_note(dict[str,object], str) -> str`, `_grouped_decisions(list[Decision]) -> str`, and the three exporter signatures are used consistently across tasks. Frontmatter facet set matches the spec exactly (`id,status,repo,agent,timestamp,validation_score,git_hash,files,symbols`); `tags`/`linked_decisions` in the body. Status order `active,draft,superseded,deprecated` matches `Decision.Status`.

**Known follow-ups (out of scope):** facet-filtered retrieval (needs an `embeddings` schema change); sub-grouping Architecture/Summaries by `tags`.
