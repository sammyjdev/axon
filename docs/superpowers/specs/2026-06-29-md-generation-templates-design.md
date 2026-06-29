# MD Generation Templates — Design (sub-project B)

**Status:** approved (brainstorming), awaiting implementation plan.
**Relation:** sub-project B of the Markdown work. Sub-project A (committed
`e87ee9e..f72a5ae`) made the *chunker* structure-aware. B makes the *generators*
emit Markdown that chunks and retrieves well, and teaches the chunker to ignore
YAML frontmatter.

## Goal

Make AXON's generated vault Markdown retrieval-friendly: the embedded content of
a generated note should be the *signal* (the decision summary, grouped decision
lists), not structural metadata or a breadcrumb that repeats the filename.

## Why (evidence)

Re-indexing the corpus after sub-project A surfaced concrete defects in the
docs produced by `obsidian/exporter.py` (93 indexed chunks under
`<vault>/AXON/{Decisions,Architecture,Summaries}/`):

- **Redundant breadcrumb.** `export_adr` writes H1 `# {id} — {summary}` to a file
  named `{id}.md`. The chunker prepends `<stem> > <heading>`, yielding symbols like
  `dec-072 > dec-072 — feat: lembrete acionável …` — the id appears twice and adds
  no retrieval value.
- **Metadata drowns the signal.** A Decision note is almost entirely metadata
  bullets (Status/Repo/Agent/score/git_hash) plus `## Files` / `## Symbols` lists.
  The only prose is `Decision.summary` (≤ 80 chars). All of that metadata is
  embedded, diluting the vector.
- **Link-list docs.** Architecture/Summaries are flat lists of `[[id]]` wikilinks
  with no prose — weak retrieval units (e.g. a 62-char `Architecture — … > Decisions[0]`
  chunk).

## Scope

**In scope**
- `src/axon/obsidian/exporter.py` — the three indexed vault generators
  (`export_adr`, `export_architecture_doc`, `export_project_summary`).
- `src/axon/embedder/md_chunker.py` — strip a leading YAML frontmatter block so it
  is neither embedded nor emitted as a junk preamble chunk.

**Out of scope (evidence-based)**
- `src/axon/adr/draft_pool.py`. Drafts are explicitly *not* indexed, and promotion
  (`pb adr review --promote`, `cli/pb.py:1798`) does `read_draft → ADR →
  store.save_adr` (SQLite) then unlinks the file — promoted ADRs never become vault
  Markdown. So `draft_pool`'s format has zero effect on retrieval. (The original
  sub-project B note named it; the evidence retires it from this scope.)
- The ~248 hand-written ADR chunks under repos' `docs/adr` / `docs/decisions` are
  not produced by these generators. They are not edited here, but they *do* benefit
  from the chunker frontmatter-skip on re-index.
- Promoting frontmatter facets (status/tags) to filterable DB columns: the
  `embeddings` table has no facet columns; that is a schema change and a separate
  future enhancement.

## Confirmed design decisions

1. Decision-note metadata moves to **YAML frontmatter** (filter facets, not
   embedded); the embedded body is **prose** only.
2. Decision-note H1 is **`# {summary}`** (drop the id) → breadcrumb `{id} > {summary}`,
   no redundancy, and the summary is lightly emphasised (appears in symbol + content).
3. `linked_decisions` render as a **`**Related:** [[a]] [[b]]`** line in the body
   (keeps the Obsidian graph working); not duplicated into frontmatter.
4. Architecture/Summaries stay **indexed** but are **restructured**: grouped into H2
   sections by `Decision.status`, each entry `- [[id]] — summary`; empty groups omitted.
5. Frontmatter handling lives in the **chunker** (a document property), reusing the
   frontmatter regex shape from `core/decision.py`.

## Components

### C1 — Chunker: skip leading frontmatter (`md_chunker.py`)

Add a private helper:

```
def _strip_frontmatter(source: str) -> tuple[str, int]:
    """Return (body, line_offset). If `source` opens with a YAML frontmatter
    block (`---\n … \n---`), return the body after it and the number of lines
    the block consumed (so chunk line numbers stay correct). Otherwise return
    (source, 0)."""
```

- Match only at the very start (`\A---\n(.*?)\n---\n?`, `re.DOTALL`, non-greedy),
  the same shape as `core/decision.py:_FRONTMATTER_RE`.
- `chunk_markdown` calls `_strip_frontmatter(source)` first, parses/packs/splits the
  returned body, then adds `line_offset` to every emitted chunk's `start_line` and
  `end_line`.
- `parse_sections` stays frontmatter-agnostic (single responsibility = heading
  structure).
- Edge cases: no frontmatter → `(source, 0)`; malformed/unterminated frontmatter
  (`---` with no closing `---`) → treat as no frontmatter, never raise; a `---`
  thematic break mid-document is untouched (only `\A` matches); a frontmatter-only
  source (no body) → existing empty-source fallback path.

The breadcrumb token budget (`MAX_TOKENS - estimate_tokens(crumb)`) and all
sub-project A invariants are unchanged.

### C2 — Decision note (`export_adr`)

Render frontmatter + prose body via a private helper in `exporter.py`:

```
def _render_note(frontmatter: Mapping[str, object], body: str) -> str:
    front = yaml.safe_dump(dict(frontmatter), sort_keys=True, allow_unicode=True).strip()
    return f"---\n{front}\n---\n\n{body}\n"
```

`export_adr(decision, *, vault)` builds:
- **frontmatter (facets, not embedded):** `id, status, repo, timestamp` (ISO),
  `validation_score`, `git_hash`, `files` (as POSIX strings), `symbols`.
- **body (embedded prose):** `# {summary}`; then, when `decision.tags` is non-empty,
  a line of Obsidian hashtags `{" ".join(f"#{t}" for t in tags)}` (feeds the tag graph
  and adds light topical signal — per the chosen "body = summary (+ tags)"); then, when
  `decision.linked_decisions` is non-empty, a blank line and
  `**Related:** {" ".join(f"[[{d}]]" for d in linked)}`.
- Writes atomically to `<vault>/AXON/Decisions/{id}.md` via the existing
  `_atomic_write`.

No new module: only `exporter.py` consumes `_render_note` (draft_pool is out).
`Decision.to_markdown` stays as-is (used elsewhere for round-trip); we do not couple
vault presentation into the core model.

### C3 — Architecture & Summaries (restructure)

Both gain frontmatter (e.g. `kind`, `repo`/`name`, `generated` timestamp) and a body
grouped by status. Shared private helper:

```
def _grouped_decisions(decisions) -> str:
    # H2 per non-empty status in a fixed order (active, draft, superseded,
    # deprecated); under each: "- [[{id}]] — {summary}". Returns the body string.
```

- `export_architecture_doc(decisions, *, vault, name)` → `# Architecture — {name}` +
  `_grouped_decisions(decisions)`.
- `export_project_summary(repo, since, decisions, *, vault)` → filter to
  `timestamp.date() >= since` (unchanged), then `# Summary — {repo}` +
  `_grouped_decisions(recent)`.
- Empty groups omitted; entirely-empty doc renders `_None._` under the H1.

## Data flow

`Decision` (SessionStore) → `exporter` (frontmatter + grouped/prose body) →
`_atomic_write` to `<vault>/AXON/…` → `index_path` → `chunk_source` →
`chunk_markdown` → `_strip_frontmatter` → `parse_sections` / `pack_sections` /
`split_text` → clean, summary-focused chunks → embeddings.

## Error handling

- Writes stay atomic (temp file + `os.replace`), as today.
- `yaml.safe_dump(..., allow_unicode=True)` — pt-BR content must survive.
- `_strip_frontmatter` never raises on malformed input; it degrades to "no
  frontmatter".

## Testing (TDD)

**Chunker (`tests/embedder/`)**
- `_strip_frontmatter` / `chunk_markdown`: (a) doc with frontmatter → no `---`/YAML
  in any chunk content and `start_line` points at the true body line; (b) doc
  without frontmatter → byte-identical chunks to before; (c) frontmatter-only doc →
  graceful (no crash); (d) unterminated `---` → treated as body, no crash.
- Sub-project A suite stays green (41 MD tests + full embedder suite).

**Exporter (`tests/obsidian/`)**
- `export_adr`: frontmatter carries the facets (id/status/repo/timestamp/score/
  git_hash/files/symbols) and *not* tags; body is `# {summary}` (no id); a decision
  with `tags` emits the `#tag` line and one with `linked_decisions` emits the
  `**Related:**` line. Feed the written file through `chunk_source` and assert the
  symbol is `{id} > {summary}` and the content contains no YAML and no metadata bullets.
- `export_architecture_doc` / `export_project_summary`: H2 groups by status in the
  fixed order, `- [[id]] — summary` entries, empty groups omitted, frontmatter
  present; empty input → `_None._`.
- Integration: a generated decision note → `chunk_source` → embedded content is
  summary-focused (no facets).

## Migration (operational, not a unit test)

After implementation, re-index the affected docs onto the new templates + chunker:
invalidate `file_index` for the relevant `.md` and re-run `pb index` (vault) — same
mechanism as sub-project A. Verify: `AXON/Decisions` symbols become `{id} > {summary}`
(no doubled id); embedded content no longer contains metadata bullets; hand-written
ADRs with frontmatter no longer carry the YAML block in their chunks.

## Out of scope / future

- Facet-filtered retrieval (status/tags as queryable columns) — needs a store schema
  change.
- Sub-grouping Architecture/Summaries by `tags` (status grouping ships first).
- Any `draft_pool` / ADR-promotion change.
