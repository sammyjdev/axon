# Closeout: Codex memory and the keyed store

Single plan of record. Replaces `2026-09-13-consolidated-closeout.md`,
`2026-09-13-codex-rollout-parser.md` and `2026-09-13-embeddings-key-resolver.md`, and
supersedes the ordering of the three source plans it consolidates:

| Short name | Plan | Angle |
|---|---|---|
| P-CODEX | `~/.claude/plans/2026-09-13-fechamento-migracao-codex.md` | Codex as primary rail; three stacked layers hide why Codex has no memory |
| P-SEQ | `docs/plans/2026-09-13-pending-sequence.md` | what runs after PR #204, in which order |
| P-KEY | `docs/plans/2026-09-13-finish-the-keying-flow.md` | the last keying defect (`embeddings`) plus the no-code cleanups |

Two documents feed them and stand unchanged: `docs/plans/2026-09-13-dead-dirs-key-analysis.md`
(rules R1..R8, the 216-row table) and `docs/plans/2026-09-13-activity-history-llmops.md`.
Every number below was measured on 2026-09-13 and re-measured by an independent agent;
the ones that change at run time are re-counted by the acceptance command, never copied.

---

## 1. Spec

### 1.1 Goal

Two outcomes, both measured and neither interpretable:

1. **Codex has memory.** An interactive Codex session in an onboarded repo writes one
   `session_memory` row keyed by repo identity and receives `.axon/context.md` at start.
   While this is false, promoting Codex to primary orchestrator trades a rail with
   context continuity for one without.
2. **The store is keyed.** No `repo`/`project` value in `sessions`, `session_note`,
   `outcome_record` or `embeddings` names something that is not a repository, and
   nothing the test suite wrote is still in the operator's data.

### 1.2 Requirements and acceptance criteria

Each requirement is closed by the command in its acceptance column, run by the step that
names it. `N` is the parsed turn count of a Codex rollout; `session_save` keeps the last
50 turns before storing (`pb.py:1320`), so every row check uses `min(N, 50)`.

| ID | Requirement | Acceptance (measured) | Closed by |
|---|---|---|---|
| **CM-1** | `parse_transcript_turns` returns the user and assistant turns of a Codex rollout (`response_item` / `payload.role`, blocks `input_text` / `output_text`), excludes `developer`, and leaves the Claude Code shape untouched | `pytest tests/memory/test_transcript.py -q` green (13 existing + 2 new); a real rollout with an assistant turn parses to > 0 turns | Task 2 |
| **CM-2** | The installed (pipx) binary captures a Codex rollout end to end without a Codex session | `axon session-hook` fed one real rollout writes exactly one `session_memory` row with `project='axon'` and `raw_turns = min(N, 50)` | Step O7 |
| **CM-3** | All 17 Codex hook entries are trusted, including the 9 that are inert today | `test_codex_hooks_parity.py` exits 0 and its last line is `OK: installed, consistent with /Users/samdev/.codex/hooks.json, and trusted` | Step O3 |
| **CM-4** | An interactive Codex session in `~/dev/axon` writes the row and receives the context | after the session, a row above `$BEFORE` has `project='axon'` and `raw_turns = min(N, 50)`; no row above `$BEFORE` keys to anything but `axon` (more than one row is expected if Codex fires Stop per turn, which O8.2 measures); the first turn answers from `.axon/context.md` without reading a file | Step O8 |
| **KS-1** | No `repo`/`project` in `sessions`, `session_note`, `outcome_record` names a non-repository | `count(*) where repo like '/%'` = 0 on `sessions` and `session_note`; `outcome_record` holds no `outcome_target` or `samdev`; `2420891be51e` reads `merit` | Step O4 |
| **KS-2** | Test-written artifacts are gone and real rows are intact | purge dry run reports 0 and 0; `~/vault/knowledge/handoffs/` holds 2 briefs; totals move by exactly sessions -1, session_note -1, outcome_record -22, decisions -6, and embeddings -6 on D1's delete branch (unchanged on the audit branch) | Step O4 |
| **KS-3** | The embeddings resolver refuses before any filesystem probe (vault by root prefix, scratch roots by denylist), keys a gone directory from its path text by exact segment match, and never returns a basename | the red tests in Task 1 pass; the dry run's refused file names only `$AXON_VAULT/**`, `gnomon-eval-src` and scratch roots | Task 1, Step O9 |
| **KS-4** | The rekey report accounts for every row, and a refused row is never written | `changed + unchanged + refused` equals `count(*) from embeddings` taken at run time; the refused rows' `(id, project)` pairs are identical before and after `--apply` | Task 1, Step O9 |
| **KS-5** | After the backfill no `project` value is a bare directory name | the anchored grep in O9 leaves only refused values (vault basenames, `gnomon-eval-src`); the 8 rows under `src/axon/vault/` read `axon`; one root per key is proven by construction for live directories (git toplevel) and by the fixture test in Task 1 for dead ones | Step O9 |
| **RC-1** | The work-notes content is not reachable outside `ctx=work` | either its 6 `embeddings` and 1 `file_index` rows are gone and the file is outside `$AXON_VAULT`, or an audit confirming non-restricted content is recorded in the handoff | Step O4 |
| **MG-1** | The keying fix and the parser land whether or not the Codex pilot works | a merged PR containing Task 1 and Task 2 exists, opened from the Codex TUI or from the current rail, and the handoff records which and why | Step O6 |
| **MG-2** | Nested `codex exec` from a dispatched `forge` agent is measured, not assumed | ADR-0004 gains a dated GO/NO-GO line with the cli version | Step O5 |
| **OB-1** | The three hook observations only visible once the payload flows are recorded | the handoff carries: whether `codex exec` fires Stop hooks, whether the quality hooks read the Codex dialect, that `compact-hook` is a no-op under Codex | Step O8 |
| **OP-1** | Every step that writes or deletes rows, or drops git state, has a backup command before it and a delta check after it | O4, O9 and O11 blocks | cross-cutting |

### 1.3 Measured baseline (2026-09-13)

| Fact | Value | Why it matters |
|---|---|---|
| Codex hook entries / trusted / inert | 17 / 8 / 9; inert: `stop:1..3`, `session_end:1`, `session_start:1`, `post_tool_use:1`, `pre_tool_use:2..3`, `post_compact:0` | the 9 are exactly the AXON and quality hooks |
| Codex sends `transcript_path` on Stop and SessionEnd | yes: hooks reference (learn.chatgpt.com/docs/hooks) and `codex-cli 0.153.4` binary strings | P-CODEX layer 3 is closed without a probe |
| table the hook writes | `session_memory(project, summary, raw_turns)`; `sessions.agent` comes only from the MCP tool `axon_session_start` | P-CODEX's checkpoint query looked at the wrong table |
| who else writes that table | Claude Code's Stop hook, per turn, from `~/dev/axon` and from every linked worktree (all key to `axon`) | probes need an empty workspace and a `raw_turns` pin |
| `raw_turns` cap | 50 (`pb.py:1320`); 97 of 2,652 rollouts on disk exceed it | the pin is `min(N, 50)` |
| installed parity check | has no `untrusted` state; PR #58 adds it | trust cannot be verified before #58 is merged and pulled |
| PRs #57, #58 (claude-skills) | OPEN, MERGEABLE, CI 2/2; #54 (installer) MERGED 21:54 | P-KEY's "unpushed branch" blocker is gone |
| `~/.claude` working tree | `role_argv.py`, `test_role_argv.py`, `settings.json` modified by another session; `main == origin/main` | #57 touches `role_argv.py`: settle before merging |
| committed forge ledger | `.forge/sdd/progress.md` is tracked on `master` and marks "Task 1" and "Task 2" complete for the isolation plan; the ledger path carries no plan identifier | a `forge plan` run on this file would skip both tasks and open an empty PR unless the ledger is renamed first (O6.1) |
| dead-directory session rows | 5 `sessions` + 2 `session_note`; `session_note` 4 (merit worktree) and 6 (bench arm) hold real notes | delete-all (P-KEY) and rekey-all (P-CODEX) are both wrong |
| `embeddings` rows | 24,164 total, 233 distinct `project`; 16,386 live dir / 7,324 gone with basename = project (silent no-op) / 454 gone with wrong basename | the backfill needs a new resolver and a three-bucket report |
| vault rows in `embeddings` | 1,815 under `$AXON_VAULT` (696 under live directories); today's dry run would write `project='vault'` to 693; the substring `/vault/` also matches 8 rows of `src/axon/vault/*.py` | vault refusal by root prefix, before git |
| `outcome_record` | 68 rows: axon 40, `outcome_target` 22 (fixture), gnomon-eval 4, claude-skills 1, `samdev` 1 | P-KEY's "44 real rows" is 46 |
| work-notes | 6 `embeddings` (personal 4, knowledge 2) + 1 `file_index` row naming `~/vault/work-notes/…`; the file moved to `~/vault/inbox/2026-07-16-definicao-do-roadmap-e-entregas.md`, work-category frontmatter, unindexed today | deleting rows without moving the file is undone by the next `index-vault` |
| `decisions` | 954; purge selects 6 | delta check in O4 |
| handoff briefs | 122 in `~/vault/knowledge/handoffs/`; purge selects 120; 2 real survive (one has `## From this session`, one cites no fixture) | KS-2 |
| forge under Codex | ADR-0004: nested `codex exec` GO from an interactive session with approval per dispatch, NO-GO under a sandbox; not measured from a dispatched agent; `codex exec` has no `--agent`; `forge_emit.py --check` all five harnesses in sync | Step O5 |
| forge plan-mode contract | runs every `### Task N:` of one file in one worktree, one PR, one commit per task; the last task's brief ends at `## Self-Review`; resumes from `.forge/sdd/progress.md`; a task blocked after its cap is a full-pass STOP; a worktree does not see untracked files | the Task/Step convention and the file order in section 3 |
| `.claude/loop.yaml` gate | `ruff check` on a subset `&& pytest -q`; `ruff check tests/scripts` is red for files no task touches | task acceptance names files, never directories |
| `session_memory` keys | 263 distinct over ~2,250 rows; `samdev` 155 (home basename), `maker-bench` 52; 15 `samdev` rows in the last 24 h | out of this plan on purpose (follow-up F1) |

### 1.4 Assumptions

- **A1.** Codex delivers `transcript_path` on Stop and SessionEnd, as its reference
  documents and its binary suggests. Verified live at O8; if false, follow-up F2 becomes
  a task and nothing else waits for it.
- **A2.** Plan files that forge executes are committed first (`d372c38` did this for the
  isolation plan); this file and the dead-dirs analysis are committed before O6, and the
  previous plan's ledger is renamed in the same commit.
- **A3.** The Codex pilot does not need Codex memory: forge's agent dispatch and the AXON
  hooks are independent mechanisms. The pilot session itself is never captured, because
  the parser it needs lands in the PR it produces and the pipx rebuild is O7; O8 is the
  first session that can be.
- **A4.** One PR carrying Task 1 and Task 2 as two commits is acceptable; plan-mode commits
  one commit per task, so the subjects stay separate in history. The cost is plan-mode's
  full-pass STOP: a task blocked after its cap strands the other. O6.3 names the way out.
- **A5.** The pipx binary is a snapshot; it is rebuilt from a pulled `master` after the PR
  merges, or the hooks keep running the old parser with no warning.
- **A6.** No Claude Code session is open in `~/dev/axon` or any of its worktrees while O7
  and O8 run.
- **A7.** `AXON_PG_URL` and `AXON_VAULT` (default `~/vault`) are set in the operator's
  shell; every command below reads them. The ROUTER list is read from `AXON_ROUTER_MD` or
  `~/.claude/axon/ROUTER.md`.

### 1.5 Out of scope

Union of the three source plans' lists, unchanged: the Max plan decision (two measured
weeks first); Cursor, Cline, Kiro, Devin; versioning `~/.codex/` or moving hooks to
`~/.agents/hooks/`; a multi-harness hook emitter; `recall_embeddings` (a self-contained
eval corpus); a `source` column separating vault rows from repo rows (1,113
`vault/AXON/Decisions` rows split across two `ctx` values, a real gap, a separate
concern); an `exec` entry point for the forge envelope under Codex; re-keying
`session_memory`, `file_index` or `recall_embeddings`; PR #179 and issues #202, #200,
#196, #187.

---

## 2. Decisions required from the operator

The loop must not make these. Each carries the recommendation and its reason; override
by editing the line. Step O1 records them.

- [x] **D1. Work-notes content.** (2026-09-14: both halves; file moved to `~/work-notes/`.) Recommend **both** halves: delete the 6 `embeddings`
  and 1 `file_index` rows, and move
  `~/vault/inbox/2026-07-16-definicao-do-roadmap-e-entregas.md` outside `$AXON_VAULT`
  (or under the `work` ctx root). Reason: today it is reachable by `search_code` and `ask`
  without the explicit `ctx="work"` the restricted rule requires; deleting rows alone is
  undone by the next `index-vault`. Alternative: read the file, confirm it is not
  restricted, record that in the handoff, skip both halves. (P-SEQ D1, P-KEY A0)
- [x] **D2. Refusals and known names.** `dev/gnomon-eval-src` (31 rows) is refused, not
  aliased. Any path under `$AXON_VAULT` gets no repo key, live directory or not, matched by
  prefix on the resolved root, never by the substring `/vault/`. `_bench`, `_worktrees`,
  `_wt` and `pytest-of-*` are a denylist. Known repository names are the ROUTER list plus
  exactly `linkedin-content-manager` and `revvo` (repositories, and the drift guard never
  reads `embeddings.project`). `config/projects.json` is an alias table only: read as a
  source of names it admits `poc-medicamentos-ia` and `iago-server`, which nothing
  classified. (P-SEQ D2, P-KEY B, dead-dirs analysis)
- [x] **D3. Benchmark-arm rows** (`sessions` `b04c37db5cfc`, `session_note` 6). Recommend
  **delete** after export. Reason: the note is a real eval report, but no repository owns
  it; re-keying to the arm name (P-CODEX) satisfies the `LIKE '/%'` guard while keeping a
  non-repository name in the store.
- [ ] **D4. Pilot shape for forge under Codex** (2026-09-14: O5 not run; this pass went on the D5 rail, so D4 carries to the next plan.) (P-CODEX C7). Recommend the **Codex TUI
  with per-dispatch approval**, gated on Step O5's dispatched-agent probe, not on the
  2026-07-10 human-typed result. An `exec` entry point stays out of scope.
- [x] **D5. Rail for the loop run if the pilot cannot start.** Recommend the current
  Claude-orchestrated `forge plan`; the maker is off the Anthropic family per forge's
  registry either way.
- [x] **D6. Uncommitted files in `~/.claude`** (2026-09-14: four files, not three; the three forge ones parked on `forge/codex-profile-flag` (f152086), `settings.json` kept as live preference, `.bak` removed.) (`agents/forge/scripts/role_argv.py` with a
  codex `-p forge` profile flag, `agents/forge/tests/test_role_argv.py`, `settings.json`)
  and the untracked `agents/forge/telemetry.jsonl.bak-20260913T215508`. Commit on a branch
  or discard, before O2.
- [x] **D7. Four stashes.** (2026-09-14: all four exported to `~/backups/stashes/` and read. axon `stash@{1}` is three pt-BR comment translations plus CLAUDE.md pointers to a `docs/agents/` that never landed; the claude-skills pair is the 2026-07-11 dash-cleanup WIP, already on main via #b34f443 and later. Verdict: drop all four; dropped by the operator on 2026-09-14, both stash lists empty.) axon `stash@{0}` (superseded plan edit, +45/-8): drop.
  axon `stash@{1}` (`wip(oss-branch)`, +4/-4 on one comment): drop after one look.
  claude-skills `stash@{0}` and `stash@{1}`: identical -1003/+12 on
  `skills/design-taste-frontend/SKILL.md`; inspect one, drop both or apply one. Dropping is
  irreversible once the reflog expires; O11.3 exports each as a patch first.

---

## 3. Execution model

**Two kinds of unit, one file.**

- **Task N** (`### Task N:` heading, `- [ ] **Task N` checkbox): code, executed by the
  loop. `forge plan docs/plans/2026-09-14-closeout-spec-and-tasks.md` detects exactly
  these, runs them in order in one worktree, one commit each, one PR, and resumes from
  `.forge/sdd/progress.md` if interrupted. It never merges.
- **Step On** (`### Step On:` heading): operator work, interactive or irreversible, never
  run by the loop. Forge's plan-mode extracts `### Task N:` headings only, which is why
  the operator units carry a different word.

Both kinds carry subtasks (`N.m`), dependencies and an acceptance block. A subtask
checkbox is ticked by whoever ran it; the requirement IDs trace back to section 1.2.

**File order is part of the safety.** The loop tasks are the last sections of this file
and end at `## Self-Review`, the terminator plan-mode names for the last task's brief. If
the operator steps followed the tasks, the last task's brief would carry the live-store
`DELETE`s and `--apply` lines to the maker. Keep sections 4 to 6 above section 7.

**The ledger carries no plan identifier.** `.forge/sdd/progress.md` is committed on
`master` from the isolation plan and marks "Task 1" and "Task 2" complete; a run on this
file would resume past both. O6.1 renames it before the pass starts.

**Order.** The two code tasks run in one forge pass (Step O6). That pass is the Codex
pilot when Step O5 says GO, and the current rail otherwise, which is a change from the
source plans: P-CODEX put the pilot after the memory checkpoint, and P-KEY tied it to the
resolver alone. Neither dependency is technical (A3, A4); collapsing them removes one PR,
one pipx rebuild and one waiting state, and the fallback is the same file on another rail.
The resolver is Task 1 because it is the larger change and the one MG-1 must not lose to
a full-pass STOP; the parser is Task 2 because it is fifteen lines and two tests.

```
D6 -> O2 -> O3 -> O8                 (merge, pull, trust, checkpoint)
D4, D5, O5 -> O6                     (nesting probe, then the loop pass = Task 1 + Task 2)
O6 merged -> O7 -> O8                (pull, pipx, pipeline probe, checkpoint)
D1, D2, D3 -> O4                     (Phase A applies; independent, may run first)
O4, O6 merged -> O9                  (embeddings backfill)
O2 -> O10                            (Gemini 3.8 campaign)
O9 -> O12                            (activity-history plan)
O11 runs whenever
```

**Loop tasks** (section 7, where their checkboxes live so that forge's structural
parser attributes nothing above them to a task): Task 1 is the embeddings resolver,
Task 2 is the Codex rollout parser.

**Operator steps** (section 4): O1 decisions and working tree, O2 merges, O3 trust, O4
Phase A applies, O5 nesting probe, O6 the loop pass, O7 pipx and pipeline probe, O8
checkpoint, O9 backfill, O10 Gemini 3.8, O11 housekeeping, O12 activity-history.

---

## 4. Operator steps

### Step O1: record the decisions and settle the claude-skills working tree

**Requirement:** none directly; unblocks O2 and O4. **Depends on:** nothing.

- [x] **O1.1** Tick D1..D7 in section 2, editing any line the operator overrides.
- [x] **O1.2** D6: in `~/.claude`, either commit the three modified files on a branch or
  discard them; decide the untracked telemetry backup.

**Acceptance:** `git -C ~/.claude status --short` prints nothing, or only lines the
operator chose to keep; D1..D7 are ticked.

### Step O2: merge #57 and #58 in claude-skills, pull

**Requirement:** feeds CM-3. **Depends on:** O1 (D6). Auto mode refuses the merge
(`Merge Without Review`), so the operator merges.

- [x] **O2.1** Merge #57 (`agy --effort` for models without a tier suffix; 3.8 becomes
  dispatchable, the pin stays 3.7 until O10 measures).
- [x] **O2.2** Merge #58 (the parity check's `untrusted` state, read from
  `~/.codex/config.toml`; it changes only `hooks/test_codex_hooks_parity.py`, so it does
  not move `~/.codex/hooks.json` and does not invalidate trust).
- [x] **O2.3** `git -C ~/.claude pull --ff-only`. The claude-skills gate is now red until
  O3; that is the finding, not a regression.

**Acceptance:**
```bash
for n in 57 58; do gh pr view $n --repo sammyjdev/claude-skills --json state -q .state; done   # MERGED, MERGED
git -C ~/.claude rev-list --left-right --count origin/main...HEAD                            # 0 0
python3 ~/.claude/hooks/test_codex_hooks_parity.py; echo exit=$?                             # exit=1, 9 lines "FAIL: untrusted: ..."
```

### Step O3: trust the 9 hooks (interactive, by design)

**Requirement:** CM-3. **Depends on:** O2. Before #58 is pulled the installed check
prints `OK: installed and consistent with ...` regardless of trust, the sixth lying green
P-CODEX records, so this step's acceptance is meaningless earlier.

- [x] **O3.1** Run `codex` in the TUI, open `/hooks`, review and trust every entry. Trust
  is keyed `<file>:<event>:<entry index>:<hook index>` in `config.toml`, so do this after
  any change to `~/.codex/hooks.json`; nothing in this plan changes that file. Any future
  edit to `~/.claude/hooks/codex-hooks.reference.json` reorders indices on install and
  needs a re-trust.

**Acceptance:** (CM-3)
```bash
python3 ~/.claude/hooks/test_codex_hooks_parity.py; echo exit=$?
# exit=0 and the last line is exactly:
# OK: installed, consistent with /Users/samdev/.codex/hooks.json, and trusted
```

### Step O4: Phase A applies

**Requirements:** KS-1, KS-2, RC-1, OP-1. **Depends on:** O1 (D1, D2, D3). Independent
of the Codex track; may run first.

- [x] **O4.1 Backups.** The briefs are in a git repo inside the vault; the tables are not.
  ```bash
  mkdir -p ~/backups && cd ~/dev/axon
  pg_dump "$AXON_PG_URL" -t decisions -t sessions -t session_note -t outcome_record -f ~/backups/axon-pre-O4-$(date +%Y%m%dT%H%M).sql
  psql "$AXON_PG_URL" -c "\copy (select * from embeddings where file_path like '%/vault/work-notes/%') to '$HOME/backups/work-notes-embeddings.csv' csv header"
  psql "$AXON_PG_URL" -c "\copy (select * from file_index where file_path like '%/vault/work-notes/%') to '$HOME/backups/work-notes-file-index.csv' csv header"
  psql "$AXON_PG_URL" -c "\copy (select * from session_note where id=6) to '$HOME/backups/bench-session-note-6.csv' csv header"
  psql "$AXON_PG_URL" -c "\copy (select * from sessions where id='b04c37db5cfc') to '$HOME/backups/bench-session.csv' csv header"
  psql "$AXON_PG_URL" -Atc "select (select count(*) from sessions), (select count(*) from session_note), (select count(*) from outcome_record), (select count(*) from embeddings), (select count(*) from decisions);"   # note the five totals
  ```
- [x] **O4.2 Purge the test artifacts** (P-KEY A1): dry run, read it, apply.
  ```bash
  python3 scripts/purge_test_artifacts.py                 # 6 decisions, 120 briefs
  python3 scripts/purge_test_artifacts.py --apply --all
  ```
- [x] **O4.3 Correct, delete, then script**, in one transaction that stops on the first
  error. The merit `update` must precede `rekey_sessions.py`, which would otherwise write
  `agent-issue-6` and hide the row from the `LIKE '/%'` guard for good (P-CODEX B5).
  ```bash
  psql "$AXON_PG_URL" -v ON_ERROR_STOP=1 --single-transaction <<'SQL'
  -- what has value and an owner is corrected by hand, before the script
  update sessions     set repo='merit'    where id='2420891be51e';      -- 1
  update session_note set project='merit' where id=4;                   -- 1
  -- D3: what no repository owns is deleted (exported in O4.1)
  delete from session_note where id=6;                                  -- 1
  delete from sessions where id='b04c37db5cfc';                         -- 1
  -- P-KEY A3 and A4
  delete from outcome_record where project='outcome_target' and summary='shipped feature';   -- 22 today, re-count
  update outcome_record set project='axon' where project='samdev';                            -- 1
  -- D1, only on the delete branch
  delete from embeddings where file_path like '%/vault/work-notes/%';                         -- 6
  delete from file_index where file_path like '%/vault/work-notes/%';                         -- 1
  SQL
  ```
- [x] **O4.4 D1, second half, same branch.** The file leaves the indexed tree or the next
  `index-vault` re-creates the rows:
  `mv ~/vault/inbox/2026-07-16-definicao-do-roadmap-e-entregas.md <a directory outside $AXON_VAULT, or the work ctx root>/`
- [x] **O4.5 Gate, then the script.**
  ```bash
  psql "$AXON_PG_URL" -Atc "select repo from sessions where id='2420891be51e';"   # merit, or stop here
  python3 scripts/rekey_sessions.py                       # dry run: now 3 sessions, 0 notes
  python3 scripts/rekey_sessions.py --apply --all
  ```

**Acceptance:** (KS-1, KS-2, RC-1)
```bash
python3 scripts/purge_test_artifacts.py | tail -1                                                  # 0 decisions, 0 briefs
ls ~/vault/knowledge/handoffs/*.md | wc -l                                                         # 2: the two real briefs survived
grep -L '## From this session' ~/vault/knowledge/handoffs/*.md | xargs grep -c '^- dec-'           # one file listed, printing 0: the note-less real brief cites no fixture decision
psql "$AXON_PG_URL" -Atc "select count(*) from sessions where repo like '/%';"                     # 0
psql "$AXON_PG_URL" -Atc "select count(*) from session_note where project like '/%';"              # 0
psql "$AXON_PG_URL" -Atc "select repo from sessions where id='2420891be51e';"                      # merit
psql "$AXON_PG_URL" -Atc "select count(*) from outcome_record where project in ('outcome_target','samdev');"   # 0
psql "$AXON_PG_URL" -Atc "select count(*) from embeddings where file_path like '%2026-07-16-definicao-do-roadmap%';"   # 0 (delete branch); re-run after the next index-vault
test ! -f ~/vault/inbox/2026-07-16-definicao-do-roadmap-e-entregas.md; echo exit=$?                # exit=0 (delete branch)
psql "$AXON_PG_URL" -Atc "select (select count(*) from sessions), (select count(*) from session_note), (select count(*) from outcome_record), (select count(*) from embeddings), (select count(*) from decisions);"
# against O4.1's totals: sessions -1, session_note -1, outcome_record -22, decisions -6, embeddings -6 on the delete branch
# (today: sessions 7->6, session_note 6->5, outcome_record 68->46, decisions 954->948, embeddings 24,164->24,158)
```

The totals line is what proves each DELETE hit only its rows; a zero over the deleted
predicate alone is satisfied by a DELETE that took too much.

### Step O5: the dispatched-agent nesting probe

**Requirement:** MG-2. **Depends on:** O1 (D4). ADR-0004's GO covers a human typing the
nested `codex exec` and approving the escalation; the pilot needs a dispatched `forge`
agent to issue it. The ADR's probe 2 references `$S` and `$S/wt` without creating them.

- [ ] **O5.1**
  ```bash
  S=$(mktemp -d) && git init -q "$S/wt" && codex --version      # record the version with the result
  cd ~/dev/axon && codex   # in the TUI, invoke the forge agent and hand it exactly this instruction:
  # "Run exactly this shell command, then report whether $S/wt/made-by-inner.txt exists:
  #  codex exec --skip-git-repo-check -s workspace-write -C $S/wt -m gpt-5.6-luna 'Create a file named made-by-inner.txt containing the single word: inner'"
  test -f "$S/wt/made-by-inner.txt" && echo GO || echo NO-GO
  rm -rf "$S"
  ```
  Approve the escalation when prompted.
- [ ] **O5.2** Append a dated GO or NO-GO line with the cli version to
  `~/.claude/agents/forge/docs/adr/0004-codex-nesting-pilot.md`.

**Acceptance:** (MG-2) the ADR carries the line; NO-GO routes O6 to the rail D5 picks
and closes D4 as "envelope needs an exec entry point", which stays out of scope.

### Step O6: the loop pass (Task 1 and Task 2), which is the Codex pilot

**Requirement:** MG-1. **Depends on:** O5, A2. This is P-CODEX C7. O3 is not a
dependency and buys nothing here: the pipx snapshot carries the pre-Task-2 parser until
O7.1, so the pilot session writes no `session_memory` row by construction.

- [x] **O6.1** In one commit on `master`: add this file and
  `docs/plans/2026-09-13-dead-dirs-key-analysis.md` (a worktree does not see untracked
  files), and rename the previous plan's ledger so plan-mode starts at Task 1:
  `git mv .forge/sdd/progress.md .forge/sdd/progress-2026-09-12-axon-isolation-and-keying.md`.
- [ ] **O6.2** From the Codex TUI in `~/dev/axon`, invoke the forge agent with
  `plan docs/plans/2026-09-14-closeout-spec-and-tasks.md`; approve each nested dispatch
  when prompted (or set an approval policy covering `codex exec`). Plan-mode runs Task 1,
  gate, quench, commit, ledger, then Task 2 the same way, then opens one PR.
- [ ] **O6.3 Fallbacks, one attempt each.** If O5 was NO-GO or the run stops for envelope
  reasons (nested dispatch not approved, agent not reachable, quota), record that in the
  handoff as the pilot's finding and run
  `forge plan docs/plans/2026-09-14-closeout-spec-and-tasks.md` on the rail D5 picks;
  plan-mode resumes from the ledger, so a task that already landed is not re-run. If a
  task is blocked after its cap (a full-pass STOP), the other task is stranded by
  plan-mode's contract: copy that task's section, from its `### Task` heading to
  `## Self-Review`, into a one-task file under `docs/plans/`, commit it, and run
  `forge plan` on that file. Never edit the stranded task's tests to make the pass move.
- [ ] **O6.4** Review and merge the PR (the loop never merges). Record in the handoff
  which rail produced it and whether the pilot crossed worktree, red test, gate, quench
  and PR.

**Acceptance:** (MG-1)
```bash
gh pr list --repo sammyjdev/axon --state merged --search "closeout-spec-and-tasks" --json number,title,mergedAt   # one PR
git -C ~/dev/axon log origin/master --oneline -5     # two task commits plus the ledger, above f41ea4d
```

### Step O7: pull, rebuild pipx, prove the pipeline without Codex

**Requirement:** CM-2. **Depends on:** O6 merged. **Precondition (A6):** no Claude Code
session open in `~/dev/axon` or in any path `git -C ~/dev/axon worktree list` prints;
every one of them keys to `axon` and its Stop hook writes the same table.

- [ ] **O7.1**
  ```bash
  git -C ~/dev/axon checkout master && git -C ~/dev/axon pull --ff-only && git -C ~/dev/axon log --oneline -1   # the parser commit must be reachable from this line
  pipx install --force ~/dev/axon
  ```
- [ ] **O7.2**
  ```bash
  PY=~/.local/pipx/venvs/axon-context-mcp/bin/python
  ROLLOUT=$(grep -l output_text $(ls -t ~/.codex/sessions/2026/*/*/rollout-*.jsonl) | head -1)   # one with an assistant turn
  N=$($PY -c "from axon.memory.transcript import parse_transcript_turns as p; print(min(len(p('$ROLLOUT')), 50))")
  echo "N=$N"                                                     # > 1: the snapshot parses Codex; 50 is session_save's cap, not a short parse
  BEFORE=$(psql "$AXON_PG_URL" -Atc "select coalesce(max(id),0) from session_memory")
  printf '{"hook_event_name":"Stop","cwd":"%s","transcript_path":"%s"}' "$HOME/dev/axon" "$ROLLOUT" | ~/.local/bin/axon session-hook
  psql "$AXON_PG_URL" -Atc "select id, project, raw_turns from session_memory where id > $BEFORE"
  ```

**Acceptance:** (CM-2) exactly one row, `project='axon'`, `raw_turns = N`. One hook
invocation writes one row. This exercises the pulled tree, the pipx snapshot, the parser
and the write path with a real rollout and no Codex session, so a failure here is never
confused with a trust or payload problem. Today, before Task 2, it prints `N=0`: the
correct red.

### Step O8: CHECKPOINT, goal 1

**Requirements:** CM-4, OB-1. **Depends on:** O3, O7. **Precondition:** as O7.

- [ ] **O8.1**
  ```bash
  BEFORE=$(psql "$AXON_PG_URL" -Atc "select coalesce(max(id),0) from session_memory")
  cd ~/dev/axon && codex     # interactive; first turn: "which lessons does the AXON context list?"; at least one more turn; exit
  ROLLOUT=$(ls -t ~/.codex/sessions/2026/*/*/rollout-*.jsonl | head -1)
  N=$(~/.local/pipx/venvs/axon-context-mcp/bin/python -c "from axon.memory.transcript import parse_transcript_turns as p; print(min(len(p('$ROLLOUT')), 50))")
  psql "$AXON_PG_URL" -Atc "select id, project, raw_turns, created_at from session_memory where id > $BEFORE order by id"
  ```
- [ ] **O8.2 Observations (OB-1)**, recorded in the handoff while the payload is observable:
  - does `codex exec` fire Stop hooks? After O3 every entry is trusted, so a plain exec
    answers it (the bypass flag adds nothing). Same `BEFORE`/`N` protocol:
    `codex exec --skip-git-repo-check -s read-only -C ~/dev/axon -m gpt-5.6-luna "one sentence about this repo" < /dev/null 2> /tmp/probe.log`.
    A row with `raw_turns = N` means every headless forge maker or reviewer dispatch also
    writes one (follow-up F4). No row and no `[axon]` line in `/tmp/probe.log` is
    inconclusive, not a no.
  - does Codex fire Stop once per session or once per turn? The O8.1 query answers it:
    one row, or one per turn with the last carrying `raw_turns = N`.
  - do the quality hooks read the Codex dialect? The reference documents `tool_name` and
    `tool_input` on PreToolUse and PostToolUse, so `axon-search-first.py`
    (`tool_input.file_path`) and `verify-after-edit.py` (`tool_name`) should run unchanged;
    edit one file in the session and watch for the verify hook's output.
  - `axon compact-hook` (`post_compact:0`) reads `isCompactSummary`, a Claude Code field:
    a silent no-op under Codex (follow-up F3).

**Acceptance:** (CM-4) a row above `$BEFORE` has `project='axon'` and `raw_turns = N`;
no row above `$BEFORE` keys to anything but `axon`; the first turn answered from the
injected `.axon/context.md` without reading a file. If the hook output says
`[axon] session-hook: sem transcript ('')`, A1 is false: follow-up F2 becomes a task, and
nothing else in this plan waits for it.

### Step O9: the embeddings backfill

**Requirements:** KS-3, KS-4, KS-5, OP-1. **Depends on:** O6 merged, O4 applied (O4
changes the total).

- [ ] **O9.1 Snapshot and dry run.**
  ```bash
  cd ~/dev/axon && git -C ~/dev/axon pull --ff-only
  psql "$AXON_PG_URL" -Atc "select id||'|'||project from embeddings" | sort > ~/backups/embeddings-project-pre-O9.txt
  TOTAL=$(psql "$AXON_PG_URL" -Atc "select count(*) from embeddings")
  VAULT=$(psql "$AXON_PG_URL" -Atc "select count(*) from embeddings where file_path like '${AXON_VAULT:-$HOME/vault}/%'")
  python3 scripts/rekey_embeddings_project.py --refused-out ~/backups/refused-O9.tsv | tail -4   # changed N, unchanged M, refused K: N + M + K = $TOTAL
  cut -f1 ~/backups/refused-O9.tsv | sort > ~/backups/refused-ids-O9.txt; wc -l < ~/backups/refused-ids-O9.txt   # = K
  ```
- [ ] **O9.2 Read the refused file.** Column 3 (reason) names only the vault root,
  `gnomon-eval-src` and scratch roots; `K` is `$VAULT` (1,815 today, 1,809 on D1's delete
  branch) + 31 + any scratch-root rows; `unchanged` is in the hundreds (about 622), never
  zero; the 8 rows under `src/axon/vault/` are in the changed bucket with key `axon`.
- [ ] **O9.3** `python3 scripts/rekey_embeddings_project.py --apply --all`

**Acceptance:** (KS-3, KS-4, KS-5)
```bash
psql "$AXON_PG_URL" -Atc "select id||'|'||project from embeddings" | sort > ~/backups/embeddings-project-post-O9.txt
diff <(grep -Ff ~/backups/refused-ids-O9.txt ~/backups/embeddings-project-pre-O9.txt) \
     <(grep -Ff ~/backups/refused-ids-O9.txt ~/backups/embeddings-project-post-O9.txt) && echo refused-rows-identical   # KS-4
psql "$AXON_PG_URL" -Atc "select project, count(*) from embeddings where file_path like '%/dev/axon/src/axon/vault/%' group by 1;"   # axon|8
psql "$AXON_PG_URL" -Atc "select project, count(*) from embeddings group by 1 order by 2 desc" \
  | grep -vE '^(axon|aerus-game-master-platform|\.claude|claude-usage-bar|claude-usage-bar-rs|glyph-kg|gnomon-eval|lina|lume|merit|orion-ai|pharos|pharos-backend|pharos-frontend|pitstop-os|rpg-master-ai|rtkx|linkedin-content-manager|revvo)\|'
# only refused values remain (vault basenames such as Decisions and AXON, gnomon-eval-src); never tests, docs, src, vault
# the alternation is anchored on the field separator: a bare prefix hides gnomon-eval-src behind gnomon-eval
```

### Step O10: Gemini 3.8 campaign

**Requirement:** none in section 1.2; P-CODEX C8. **Depends on:** O2.

- [ ] **O10.1** In `~/dev/tools/gate-over-model/forge-role-selection`, against the 3.7 arms
  already measured, on the straight ruler (#55 and #56 merged, telemetry purged to 250
  lines). Cost in money is zero (agy authenticates by OAuth).

**Acceptance:** measured arms for 3.8; the pin moves only on evidence, and forge refuses
to pin without a measurement.

### Step O11: housekeeping

**Requirement:** none in section 1.2 (P-KEY Phase D, P-CODEX debt); OP-1 for the drops.
**Depends on:** nothing. Remove a worktree only when `git -C <worktree> status --porcelain`
prints nothing; never `--force`.

- [x] **O11.1** axon: 17 of the 19 non-master worktrees hold branches merged to `master`
  (`agent/issue-92..198`, `fix/summary-cap-250`, both chunker fixes,
  `docs/adr-retrieval-partition`), all clean. Keep `agent/plan-axon-isolation-and-keying`
  while its untracked `.specs/` is wanted, and `fix/pack-eval-ruler-precision` (PR #179).
- [ ] **O11.2** claude-skills: `timeout-rebase` and `telemetry-guard` are merged by content
  (squash; `git branch --merged` will not show it) and clean; `agent-plan-codex-axon-hooks`
  is merged by content but holds untracked `.specs/features/plan-codex-axon-hooks/` and two
  review artifacts; `agy-effort` and `hook-trust` go after O2.
  `fix/maker-timeout-and-aging-fixture` is fully in `main` by content (`b05b5f9` is
  dangling, nothing to recover); delete it. `~/dev/_wt/scope-creep` is unrelated to this
  flow; leave it.
- [x] **O11.3** Stashes per D7, each exported first:
  `for r in ~/dev/axon ~/.claude; do for n in 0 1; do git -C $r stash show -p "stash@{$n}" > ~/backups/$(basename $r)-stash-$n.patch; done; done`
- [ ] **O11.4** (optional, claude-skills) Redo `-c mcp_servers={}` on the codex rail
  dispatch in `role_argv.py`: the other session measured 25,408 to 20,363 prefix tokens
  per dispatch and the change is gone from disk. After O2; not on the critical path.

**Acceptance:** `git worktree list` in both repos shows only the kept entries; each
removed one was clean at removal; four `.patch` files exist before any stash drop.

### Step O12: the activity-history plan

**Requirement:** none in section 1.2 (P-SEQ's third task). **Depends on:** O9.
`forge plan docs/plans/2026-09-13-activity-history-llmops.md`, AGY rail as that document
specifies. Its first task must key `ActivitySession.project` through the resolver from
Task 1 of this plan, or it re-creates the defect for a fifth table (P-SEQ's stated reason,
"its eighth task links by repository", is inverted: that plan forbids repository linking;
the coupling is `ActivitySession.project` from its first task on). Its Codex adapter task
parses the same rollout shape Task 2 of this plan teaches `transcript.py`; reuse, do not
re-derive.

**Acceptance:** that plan's own, per slice.

---

## 5. Follow-ups registered, not scheduled

- **F1. `session_memory` keys.** 263 distinct `project` values; `samdev` 155 (home
  basename), `maker-bench` 52; 15 `samdev` rows in the last 24 h. The capture path keys
  a non-git cwd by basename today: the same defect class as PR #204, in the table the
  checkpoint writes to. Needs its own decision (skip capture outside a repository, or key
  scratch roots by an explicit marker). Outside goal 2 on purpose.
- **F2. Layer-3 contingency.** `session-hook` locating the newest rollout by `cwd` in
  `~/.codex/state_5.sqlite` (`threads.rollout_path`, `threads.cwd`; 229 axon threads).
  Built only if O8 fails on an empty `transcript_path`.
- **F3. `axon compact-hook` under Codex** is a silent no-op. Quality, not memory.
- **F4. Hook noise from headless dispatches.** If O8 shows `codex exec` fires Stop hooks,
  every forge maker and reviewer dispatch writes a `session_memory` row for its worktree.
  Decide whether to suppress it (an env marker the hook honours) before Codex carries
  every maker.

## 6. The pattern this plan defends against

Seven greens lied on 2026-09-13, all the same way: a stable identifier pointing at content
that moved, or at a state that lives somewhere else. Drafts of this plan nearly added
more: an acceptance query against a table the hook never writes, a row count in a table
two harnesses write, a turn count the writer caps, a substring that names the vault and
matches the engine's own source, a parity check that reports trust before the code that
checks trust is installed, a ledger from another plan that would mark this one done. The
defence is the same every time: measure the effect, not the artifact. Every acceptance
line above is a query, a count delta or an exit code.

---

## 7. Loop tasks

Read by `forge plan`. Each section is a complete maker brief: it names every file it
edits, every test it writes, and acceptance commands that exist on `master`. The maker
never runs `--apply`, never touches the live store, never edits an existing test to make
a pass move.

- [ ] **Task 1: a path-text resolver for embeddings that refuses rather than guesses**
- [ ] **Task 2: read the Codex rollout shape in parse_transcript_turns**

### Task 1: a path-text resolver for embeddings that refuses rather than guesses

**Requirements:** KS-3, KS-4. **Depends on:** nothing in code; the fixture is built from
`docs/plans/2026-09-13-dead-dirs-key-analysis.md`, committed with this file (A2).
**Files:** `src/axon/core/repo_identity.py` (new function next to `repo_identity`),
`scripts/rekey_embeddings_project.py` (use it; account for every row; add
`--refused-out`), `tests/fixtures/dead_dirs_216.json` (new),
`tests/core/test_repo_identity.py` (or a sibling) and
`tests/scripts/test_rekey_embeddings_project.py` (exists; extend it).
`scripts/rekey_sessions.py` is not touched: after Step O4 it has no dead-directory rows.

**Why.** `rekey_embeddings_project.py` resolves each row's directory with
`repo_identity()`, which shells out to git. When the directory is gone, git fails and the
function returns the basename (`repo_identity.py:39`): 7,324 rows are gone with basename
= stored `project`, dropped from the report as if fine; 454 are gone and would get one
wrong basename written over another. When the directory exists but is the vault, git
answers `vault`: 693 rows would be written so. Applying the script as merged writes the
collapse PR #204 set out to remove (`627 rows -> project='tests'`) and keys the vault as
a repository.

- [ ] **1.1 Fixture.** `tests/fixtures/dead_dirs_216.json`: one entry per row of the
  216-row table, `{"dir": "~/dev/glyph-kg/docs", "key": "glyph-kg"}`, `"key": null` for
  the three refused rows (`gnomon-eval-src`, `vault/AXON/Decisions`, `vault/work-notes`).
  Paths keep the literal `~`; tests expand it against a fake home on `tmp_path`. Add the
  six `sessions` values from that document's second table as inline cases; among them
  `products/merit-worktrees/agent-issue-6 -> merit`,
  `_bench/maker-bench/gemini-3.7-flash-high__H3__r4 -> refused`, `dev/axon` (a live
  directory) and the bare value `axon`, which is already a key and not a path.
- [ ] **1.2 Resolver, red tests first.** A pure function in `src/axon/core/repo_identity.py`
  over the path text plus explicit arguments (known names, alias table, denylist, vault
  root). No module globals, no import from `scripts/` (`src/axon` is the shipped package,
  `scripts/` is not). Ordered steps:
  0. **Refuse before any filesystem probe.** A path under the vault root (R4), matched by
     prefix on the resolved root and never by the substring `/vault/` (which swallows
     `~/dev/axon/src/axon/vault/`, 8 rows owed `axon`), or with any segment in
     `_bench`, `_worktrees`, `_wt` (R8) or starting with `pytest-of-` (R5), is refused
     whether or not the directory exists.
  1. Directory exists: `repo_identity()`, as today.
  2. Directory gone: from the path text. R1: a repo that moved from `dev/<X>` to
     `dev/products/<X>` or `dev/tools/<X>` keeps `<X>`, matched **exactly** against the
     known names, never by prefix (prefix makes `claude-usage-bar` swallow
     `claude-usage-bar-rs`). R2: an alias key maps to its value first. R3: a nested repo
     declared in the ROUTER keys to itself, deepest matching segment wins
     (`Pharos/pharos-backend/app -> pharos-backend`). R7: `<repo>-worktrees/<branch>` keys
     to `<repo>` when known.
  3. Neither applies: **refuse**, return `None`. `dev/gnomon-eval-src` is refused, not
     prefix-matched. Never return a basename.

  Red tests, all failing before the function exists: every fixture directory keyed to its
  expected key or refused, and no result equals the basename unless that is the expected
  key; one repository root per resolved key over the fixture; a vault path whose
  directory **exists** (`<fake home>/vault/knowledge/x` inside a git repo at
  `<fake home>/vault`) is refused with a reason naming the vault root;
  `<fake home>/dev/axon/src/axon/vault/til_promoter.py` inside a live git repo keys to
  `axon`; `~/dev/_bench/x/y`, `~/dev/_wt/scope-creep`, `~/dev/_worktrees/a/b`,
  `/tmp/pytest-of-user/x`, `~/dev/gnomon-eval-src` refused; `~/dev/claude-usage-bar-rs/docs`
  keys to `claude-usage-bar-rs`; `~/dev/Pharos/pharos-backend/app` keys to `pharos-backend`;
  an empty known-names set is rejected by the resolver's caller (see 1.3), never silently
  turned into refuse-everything.
- [ ] **1.3 Script.** The caller assembles the inputs. Known names: the text of
  `os.environ.get("AXON_ROUTER_MD")` or `~/.claude/axon/ROUTER.md` passed through
  `parse_canonical_repos(text)` from `scripts/check_onboarding_drift.py` (it takes text,
  not a path), plus `linkedin-content-manager` and `revvo`; if the file is absent or the
  set comes back empty, the script exits non-zero before printing any plan, because an
  empty set would refuse every dead-directory row while the counts still add up. Aliases:
  `config/projects.json` `name -> basename(path)` (`Pharos -> pharos`,
  `PitStopOS -> pitstop-os`, `Orion-AI -> orion-ai`,
  `linkedin_content_manager -> linkedin-content-manager`, `piloto_revvo -> revvo`). Vault
  root: `AXON_VAULT`, default `~/vault`, resolved to an absolute path. Split planning from
  the DB: a pure function over rows `(id, file_path, project)` returning `changed`,
  `unchanged` (resolved key equals the stored value) and `refused` (with reason). Dry run
  and apply both print the three counts and their sum; `--refused-out <path>` writes every
  refused row as `id<TAB>file_path<TAB>reason`; a refused row is never in the UPDATE.
  Keep `--apply`, `--all`, `--embeddings`, `--only-project`. Red tests:
  `changed + unchanged + refused` equals the input length and `unchanged` rows are
  reported, not dropped; a refused row is absent from the change list and present in the
  refused output; a missing ROUTER file or an empty set exits non-zero with no plan.

**Acceptance:** (KS-3, KS-4)

```bash
rtk pytest tests/core tests/scripts -q -k "repo_identity or dead_dirs or rekey"   # new tests green
rtk pytest tests/ -q                                                                # the gate
rtk ruff check src/axon/core/repo_identity.py scripts/rekey_embeddings_project.py \
  tests/core/test_repo_identity.py tests/scripts/test_rekey_embeddings_project.py   # plus any new test file; files, never directories: tests/scripts/ carries 5 inherited findings
```

Orchestrator verification, read-only, outside the suite:

```bash
TOTAL=$(psql "$AXON_PG_URL" -Atc "select count(*) from embeddings")
VAULT=$(psql "$AXON_PG_URL" -Atc "select count(*) from embeddings where file_path like '${AXON_VAULT:-$HOME/vault}/%'")
python3 scripts/rekey_embeddings_project.py --refused-out /tmp/refused.tsv | tail -4   # changed N, unchanged M, refused K, and N + M + K == $TOTAL
cut -f3 /tmp/refused.tsv | sort | uniq -c                                              # reasons: vault root, gnomon-eval-src, scratch roots, nothing else
```

`K` is `$VAULT` (1,815 today, 696 under live directories) + 31 (`gnomon-eval-src`) + any
scratch-root rows; the 8 rows under `~/dev/axon/src/axon/vault/` appear as changed to
`axon`; `unchanged` is in the hundreds (a dry simulation of these rules gives 622: 450
live rows already keyed by their repo name plus 172 dead rows whose path text resolves to
the stored value), never zero. The `--apply` run is Step O9's, never this task's.

**Not in this task:** `recall_embeddings`, a `source` column, `file_index` rows (9 dead
directories and 2 `pytest-of-*` with rows only there), `sessions`, `session_note`,
`session_memory`.

### Task 2: read the Codex rollout shape in parse_transcript_turns

**Requirement:** CM-1. **Depends on:** nothing. **Files:**
`src/axon/memory/transcript.py`, `tests/memory/test_transcript.py` (13 tests today, all
Claude Code shape; they must keep passing unchanged).

**Why.** `axon session-hook` runs on Codex Stop and SessionEnd with `transcript_path`
pointing at a Codex rollout. `parse_transcript_turns` reads `entry["message"]["role"]`,
the Claude Code shape, and returns 0 turns on every Codex rollout measured. With 0 turns
`session_save` prints `Sessão muito curta (0 turns), skip` and writes nothing; the hook
exits 0; nothing says why.

**The Codex shape, measured on real rollouts.** One JSON object per line. Line types:
`session_meta`, `event_msg`, `response_item`, `world_state`, `turn_context`,
`token_usage_record`. Only `response_item` with `payload.type == "message"` is chat:

```json
{"type":"session_meta","payload":{"id":"...","cwd":"/Users/x/dev/axon","cli_version":"0.153.4"}}
{"type":"response_item","payload":{"type":"message","role":"developer","content":[{"type":"input_text","text":"<skills_instructions>..."}]}}
{"type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"why does the hook never fire?"}]}}
{"type":"response_item","payload":{"type":"reasoning","summary":[]}}
{"type":"response_item","payload":{"type":"custom_tool_call","name":"shell","input":"..."}}
{"type":"response_item","payload":{"type":"custom_tool_call_output","output":"..."}}
{"type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"because the parser reads the wrong shape"}]}}
{"type":"event_msg","payload":{"type":"agent_message"}}
```

- [ ] **2.1 Red tests.** Add a fixture in the shape above to `tests/memory/test_transcript.py`
  and assert `parse_transcript_turns` returns exactly
  `[{"role":"user","content":"why does the hook never fire?"}, {"role":"assistant","content":"because the parser reads the wrong shape"}]`.
  Add a second test: a `response_item` whose `payload.type` is `reasoning` and carries a
  `text` key somewhere is still not a turn (blocks are selected by type, the rule
  `test_a_thinking_block_carrying_a_text_key_is_still_not_the_turn` already pins for the
  Claude shape). Run the file: both new tests fail, the 13 old ones pass.
  Two details the fixture locks: `role == "developer"` is injected instruction text
  (skills, permissions), excluded exactly as `CHAT_ROLES` excludes `system` and `tool`;
  the blocks are `input_text` and `output_text`, never `text`, so `_text_of` as it is
  would find the turn and return an empty string.
- [ ] **2.2 Smallest change that passes.** Resolve the message dict once: `entry["message"]`
  when it is a dict, else `entry["payload"]` when `entry["type"] == "response_item"` and
  `payload["type"] == "message"`, else skip. Extend the block filter in `_text_of` to
  `("text", "input_text", "output_text")`. No new module, no format flag, no change to
  `last_compact_summary` (Codex writes no `isCompactSummary`; see OB-1).
- [ ] **2.3 Docstring.** The module docstring says Claude Code only; it is no longer true.

**Acceptance:** (CM-1)

```bash
rtk pytest tests/memory/test_transcript.py -q                                     # 15 passed
rtk ruff check src/axon/memory/transcript.py tests/memory/test_transcript.py
```

Orchestrator verification, outside the suite (reads the operator's `~/.codex`, never a
test; 24 of 2,652 rollouts on disk hold at most one message, so the selector skips them):

```bash
ROLLOUT=$(grep -l output_text $(ls -t ~/.codex/sessions/2026/*/*/rollout-*.jsonl) | head -1)
python3 -c "from axon.memory.transcript import parse_transcript_turns as p; \
t=p('$ROLLOUT'); print(len(t), [x['role'] for x in t][:6])"      # > 0, roles only user/assistant
```

**Not in this task:** the pipx rebuild (O7), trusting hooks (O3), the `session_start`
context injection, any Codex-specific branch in `session_save` or `session_hook`, Claude
Code subagent transcripts, `codex exec --json` event streams (the activity-history plan's
Codex adapter task owns those), the PostCompact path.

## Self-Review

Before the PR opens, the pass checks, for each task: the acceptance block above was run
in the worktree and its output is in the task report; only the files the task names were
edited, plus new test files and the fixture; no existing test was edited to make a pass
move; nothing was run with `--apply`, and nothing touched `$AXON_PG_URL`; the ledger line
was committed with the task. The PR body names this file and the rail that produced it.
