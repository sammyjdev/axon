# AXON - execution plan for fresh sessions

Written 2026-08-26, replacing the 2026-08-22 plan (its Phase 1 shipped). Repo:
`~/dev/axon` (`sammyjdev/axon`, default branch `master`).

This document exists so a session with zero prior context can pick up the axon
backlog and execute it in the right order. It is a sequencing decision, not a
spec. Each phase names its own source of truth; when this file and the repo
disagree, the repo wins - re-verify before acting.

## State verified on 2026-08-26

12 open issues, 0 open PRs, master at `627a119`.

What the 2026-08-22 plan's Phase 1 delivered, and what it exposed:

- `axon doctor` now has `capture.hook_interpreter`, `install.freshness` and
  `capture.gap` (PR #148). It exits 2 today on 4 of 10 repos - correctly.
- `axon seed-lessons` exists (#144), after #145 fixed it being registered on
  `pb.app` but not on the shipped `axon.__main__:app`.
- The lesson corpus is seeded: 22 rows, paraphrase retrieval verified against
  the live store.
- The FREE provider profile was **entirely dead** - all three models, all five
  roles (#154). Groq removed the llama family; NIM answers 410 for the 70b.
  The judge had been silently skipping every decision. Now `budget`, on
  DeepInfra + OpenRouter, verified live: `dec-1028` went from
  `judged: false / 0.0` to `judged: true / 4.2`.
- `onnxruntime` was loading on every single `axon` invocation for a model that
  routes over HTTP (#153), which is what produced the SIGABRT on shutdown.

The through-line of all of it: **a thing that never ran was indistinguishable
from a thing that succeeded.** Dead hooks behind `|| true`, a command absent
from the shipped app while its tests passed against another app, a judge whose
failure looked like a default score. Sequence the backlog accordingly.

## Phase 0 - outside this repo, minutes, unblocks the rest

Not issues here, but they gate the value of everything below.

- **`axon_record_lesson` / `axon_record_outcome` are not in the forge agent's
  tool allowlist** (`~/.claude/agents/forge.md` frontmatter). The loop can read
  the corpus (`axon_search_lessons` is allowed) and cannot write to it. Five
  passes' worth of lessons went to a markdown file instead. One-line fix.
- `~/.claude` has six modified files uncommitted, including the accumulated
  craft lessons and the maker scripts. Owner reviews and commits.

## Phase 1 - stop corrupting capture (do this before any forge run)

**#158 - `Decision.repo` is a filesystem basename.** `git_event.py::on_push`
derives the repo key from `_repo_root(cwd).name`, so a commit made in a git
worktree is filed under the worktree's directory name - a repo that does not
exist. It never joins the repo's decision stream and never counts toward
`detect_scope_end`, which is what actually triggers judging. Measured: 51
unjudged draft decisions for `axon` in the live store.

This is first for a dependency reason, not a severity one: **the forge works
exclusively in worktrees.** Every task run before this is fixed adds more
orphaned decisions. Fixing security while widening the capture hole is the
wrong order.

Schema change with migration implications - needs an ADR, not just a patch.
#151 was the same root cause found from the read side and is closed as a
duplicate; its context lives in #146's merged PR.

## Phase 2 - the four security/CI issues, serial

#92 (ADR inference runs an LLM over untrusted commit messages), #93 (optional
auth on `serve-http` before `--host` widens), #94 (`dashboard.py` builds HTML
via `innerHTML`), #97 (gitleaks flakiness).

    forge run --wip 1

Serial on purpose: three are security-flagged, `loop.yaml` arms the classifier
floor on `secrets`/`destructive-tools`, so they route to the Legendary tier and
produce large diffs. One at a time keeps the review surface honest.

#92 first - commit messages are attacker-influenced input in any repo that
takes outside contributions. #93 and #94 matter when, and only when, the
service stops being localhost-only.

## Phase 3 - debt from the 2026-08-25/26 round, cheap

- **#156** - migrate the `free` provider-profile call sites to `budget`. The
  alias holds until then, so this is tidying, not urgency.
- **#157** - compliance and `provider_enabled` gates run before the post-route
  budget downgrade can rewrite `result.model`. Unreachable in the default
  config until now; the `budget` profile crosses providers on downgrade, which
  is exactly what makes it reachable.
- **#143** - NIM serves 500 for `baai/bge-m3`. This is a record, not work:
  close it if NVIDIA starts serving the model it advertises.

## Phase 4 - measurement, not implementation

#56 (is the recall prefix stable enough for the prompt cache?) and #57 (measure
`ask()` compression quality and latency) are **benchmarks**. Do not hand them
to `forge task`; there is nothing to implement until the numbers exist. Run
them under the METRON protocol
(`~/dev/tools/metron/merit-graph-vs-loop/` is the reference shape).

#56 is the same item as the "KV-stable SessionStart injection" conclusion from
the 2026-08-03 context-economy research - do not open a second issue for it.

## Phase 5 - owner-only, do not delegate

#59 (errata sweep: external material still citing retired numbers) and #58
(rewrite the launch post drafts). Public claims about measured results. An
agent rewriting them unsupervised is how a wrong number gets republished.
Owner writes; an agent may only check citations against the snapshot.

## Environment notes (verified 2026-08-26)

- **Provider profile is `budget`** (`free` still resolves, via alias). Bottom
  tier `deepinfra/.../Meta-Llama-3.1-8B-Instruct-Turbo`, mid
  `openrouter/meta-llama/llama-3.3-70b-instruct`, top
  `deepinfra/.../Llama-3.3-70B-Instruct-Turbo`. The three tiers must stay
  distinct: `_fallback_model_for` is a tier downgrade reused as failover and
  returns `None` when the candidate equals the primary, so collapsing two rungs
  silently disables that tier's fallback. Five existing tests assert it.
- **Embedding chain is `deepinfra` alone**, set in `~/.zshrc`, deliberately
  without a fallback. Ollama is not on this Mac by choice; NIM returns 500 for
  every `bge-m3` payload while listing the model; Cerebras lists two models and
  bills for both.
- **A catalogue listing is not evidence a provider serves a model.** NIM lists
  `bge-m3` and `mistral-7b` and serves neither. Verify with a real call.
- **The install is a pipx snapshot, not editable** - a checkout that moves
  between branches must not become what the MCP and 13 repos' hooks execute.
  After landing on master, run `pipx install --force ~/dev/axon`.
  `install.freshness` in `axon doctor` now reports when you forget.
- The CLI is `axon`. `pb` does not exist; docs referencing `pb doctor` are stale.

## Gate and known traps (from `.claude/loop.yaml`)

- Gate: `ruff check src/axon/router src/axon/resilience tests/router tests/resilience && pytest tests/router tests/resilience tests/store tests/scripts tests/cli tests/doctor tests/config -q`
- **The ruff arm covers two source dirs only.** A green gate does not prove
  lint on your diff - run ruff separately on what you touched.
- Setup: `pip install -e ".[dev]"`
- Any CLI surface must be registered on `axon.__main__:app` and its tests must
  drive that app, not `pb.app`. This gap shipped a command that did not exist
  (#142 -> #145) past a green gate, 4 killed mutants and an approving reviewer.
- Never assert on Rich-rendered help text - it depends on terminal width, TTY
  detection and the resolved Typer/Rich versions. Assert on Click's parameter
  list via `typer.main.get_command(app)`.
- `pytest` has no `--timeout` plugin here. It can hang on exit from an unclosed
  asyncpg pool after the summary prints; read the printed counts.
- The judge runs on **push**, not on commit, and only when `detect_scope_end`
  fires.

## Recommended order

Phase 0 -> Phase 1 -> Phase 2 -> Phase 3 -> Phase 4 -> Phase 5.

Phase 1 is ahead of the security work for a dependency reason: the loop runs in
worktrees, and until #158 lands, every task it runs files its decisions under a
repo that does not exist.
