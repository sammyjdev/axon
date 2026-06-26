# Design: Prometheus Second Brain — 5 Missing Features

**Date:** 2026-05-18  
**Status:** Approved  
**Scope:** Session memory wiring, session notes, ADR inference from commits, ADR vault sync, dev project discovery

---

## Context

Audit of Prometheus as a "second brain" on 2026-05-18 revealed 5 features that exist partially in the codebase but are not wired end-to-end:

| Gap | Finding |
|---|---|
| Session memory | `SessionCompressor` and `save_session_memory()` exist but nothing calls them |
| Narrative capture | No mechanism beyond ADRs |
| ADR inference | `code_change` table in schema, never populated; no LLM inference path |
| ADR vault sync | ADRs live only in SQLite, never written to Obsidian vault |
| Dev project indexing | No `projects.json` manifest; repos not indexed in Qdrant |

---

## Feature 1: Session Memory (PostStop hook)

### Goal
Automatically persist a compressed summary of each Claude Code session to the DB so `get_session_memory(project)` returns useful context in future sessions.

### Flow
1. Claude Code PostStop hook fires when session ends
2. Hook calls `pb session save --cwd $CWD` (receives transcript via `CLAUDE_TRANSCRIPT_PATH` env or stdin)
3. `pb session save` reads the transcript JSON, extracts assistant + user turns
4. Passes turns through `SessionCompressor` (Haiku, `_COMPRESS_INTERVAL=10`, `_MAX_SUMMARY_TOKENS=400`)
5. Prompt focus: **decisions made, files changed, next steps** (not alternatives considered)
6. Calls `save_session_memory(project=basename(cwd), summary=..., raw_turns=N)`

### Project identification
`project = os.path.basename(cwd)` — zero config, inferred from working directory.

### Code changes
- `cli/pb.py` — new `pb session save --cwd` command
- `.claude/settings.json` (each project) — PostStop hook entry
- No changes to `session_store.py` or `mcp/server.py`

### Edge cases
- Empty transcript (session < 2 turns): skip silently
- Transcript too large: truncate to last 50 turns before compressing
- Haiku unavailable: log warning, skip (non-blocking)

---

## Feature 2: Session Notes (`pb note`)

### Goal
Allow free-form notes during a session, stored with timestamp and project, surfaced alongside session memory.

### Flow
- `pb note "texto livre"` → stores in new `session_note` table with `project=basename(cwd)`, `body`, `created_at`
- `get_session_memory(project)` query extended to include notes for the same project, shown as a separate "Notas" section after the compressed summary

### Schema addition
```sql
CREATE TABLE IF NOT EXISTS session_note (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project    TEXT NOT NULL,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

### Code changes
- `store/session_store.py` — `session_note` table in DDL, `save_note()`, `get_notes(project, limit)` methods
- `mcp/server.py` — `get_session_memory` updated to query and append notes section
- `cli/pb.py` — `pb note <text>` command

---

## Feature 3: ADR Inference from Commits (git post-commit hook)

### Goal
Automatically detect architectural decisions in git diffs and save ADRs without manual intervention.

### Flow
1. `.git/hooks/post-commit` calls `pb adr infer-commit --project $(basename $PWD)`
2. Command reads `git log -1 --stat` + `git diff HEAD~1 HEAD` (truncated to ~3000 tokens)
3. Calls Haiku with classifier prompt:
   > *"Does this diff contain an architectural decision (new pattern, significant deletion, technology choice, constraint)? If yes, return JSON: {title, context, decision, rationale}. If no, return null."*
4. If null: exit silently
5. If decision detected: `save_adr(project, ...)` + print `[prometheus] ADR salvo: <título>`

### Hook installation
`pb adr hook install [--path <repo-path>]` — copies hook script to `.git/hooks/post-commit`, `chmod +x`. Safe: appends to existing hook if one exists.

### Filtering
- Commits with message starting with `chore:`, `docs:`, `style:`, `test:` are skipped before LLM call (low signal, save tokens)
- Diff must touch at least 1 non-test file to trigger

### Code changes
- `cli/pb.py` — `pb adr infer-commit`, `pb adr hook install`
- New prompt template: `src/prometheus/templates/adr_classifier.txt`

---

## Feature 4: ADR Vault Sync

### Goal
Export ADRs from SQLite to Obsidian-readable Markdown files so they are navigable and linkable in the vault.

### Format
One `.md` file per project at `$AXON_VAULT/personal/adrs/<project>.md`.

File structure:
```markdown
# ADRs — <project>
_Last synced: 2026-05-18_

## <title>
**Data:** 2026-05-18
**Decisão:** ...
**Racional:** ...
**Contexto:** ...

---
```

### Sync behavior
- `pb adr sync [--project X]` — syncs all projects (or one)
- Fully idempotent: overwrites the entire file each run
- Optionally triggered automatically after `save_adr` via `--auto-sync` flag (opt-in)

### Code changes
- `cli/pb.py` — `pb adr sync`
- `store/session_store.py` — no changes (uses existing `get_adrs()`)
- New Jinja2 template: `src/prometheus/templates/adr_project.md.jinja`

---

## Feature 5: Dev Project Discovery (`pb scan`)

### Goal
Auto-discover git repos in a directory, let the user approve candidates, and update `projects.json` for `pb index-dev`.

### Flow
1. `pb scan [<dir>]` (default: `~/dev`) — walks directories up to depth 2, finds `.git/` markers
2. Filters out repos already in `projects.json`
3. For each new repo: detects dominant language (file extension count), infers ctx heuristic (`personal` default; `work` if `.work` marker file exists)
4. Interactive prompt per candidate:
   ```
   [?] linkedin_content_manager  python  ctx=personal → add? [y/n/skip]
   ```
5. Approved repos appended to `engine/config/projects.json` (created if missing)
6. After scan: `Add to index now? [y/n]` - if yes, runs `pb index-dev`

### Language detection
Count files by extension in repo root + one level deep. Top extension wins. Mapped to Prometheus `VALID_LANGUAGES` (`python`, `java`, `typescript`, `markdown`, `text`).

### Code changes
- `cli/pb.py` — `pb scan` command
- `config/projects.py` — `write_project_manifest(entries)` function (merge, no duplicates)

---

## Implementation Order

Recommended sequence (dependency-safe, value-first):

1. **Session notes** — smallest change, standalone, immediate value
2. **Session memory** — depends on `pb session save` + PostStop hook setup
3. **ADR vault sync** — standalone, high visibility in Obsidian
4. **Dev project discovery** — standalone, unblocks `search_code` for all repos
5. **ADR inference** — depends on git hook install command; most complex LLM path

---

## Out of Scope

- ADR inference from Claude Code conversation (not git commits)
- Obsidian live sync / file watcher
- Session memory for non-Claude Code tools (e.g., Cursor, terminal sessions)
- Automatic ctx resolution beyond `personal`/`work` heuristic
