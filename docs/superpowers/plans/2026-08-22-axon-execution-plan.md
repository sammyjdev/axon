# AXON - execution plan for fresh sessions

Written 2026-08-22, replacing the 2026-08-03 plan (its Phase 1 and Phase 4 are
done). Repo: `~/dev/axon` (`sammyjdev/axon`, default branch `master`).

This document exists so a session with zero prior context can pick up the axon
backlog and execute it in the right order. It is a sequencing decision, not a
spec. Each phase names its own source of truth; when this file and the repo
disagree, the repo wins - re-verify before acting.

## State verified on 2026-08-22

- 8 open issues, 0 open PRs. The lesson-node feature (Waves A-D, PRs #137-#140)
  is on master.
- Labels as found: `agent:ready` on #97, #94, #93, #92. No label on #59, #58,
  #57, #56.
- Closed since the previous plan: #45 (recall quality, bge-m3) and the whole
  `agent:blocked` set (#31, #32, #42, #74).

### What the previous plan did not cover, and cost twelve days

The gap that mattered was not in the backlog. It was between what is committed
and what actually runs:

- The 13 repos' git hooks called `~/.local/pipx/venvs/axon-mcp/bin/python`.
  `pipx uninstall axon-mcp` removed that venv on 2026-08-10 at 11:21 (see
  `~/.local/pipx/logs/cmd_2026-08-10_11.21.08.log`). Every hook ends in
  `2>/dev/null || true`, so 28 commits produced no decision and nothing said so.
  The last decision carrying a `git_hash` before the repair is from 08-08.
- The MCP server ran a `pipx` snapshot from 08-11, so `adr/lesson_pool.py`,
  `adr/retraction.py` and `adr/draft_dormancy.py` - merged the day before - did
  not exist in the process serving the tools.
- The shipped lesson corpus had never been seeded: the `lessons` table held 0
  rows while `agent-errors.json` held 18, because `seed_corpus` has no caller.

All three are repaired as of 2026-08-22. The point of Phase 1 below is that
none of them were *detectable*: `axon health` reports sqlite / pgvector / vault
/ git and cannot see any of it.

## Phase 1 - make the capture path observable (do this first)

Two new issues. They are not features; they are the reason the three failures
above lasted as long as they did.

**`axon doctor` that exercises the hooks' own interpreter.** It must answer
three questions no current command answers: does the interpreter named in each
`.git/hooks/post-commit` exist and can it `import axon.adr.lesson_pool`; does
the installed version match `HEAD` of master; and is the date of the newest
decision consistent with the date of the newest commit. Any one of the three
would have caught 08-10 on 08-10.

**`axon seed-lessons` as a real command.** `seed_corpus` is a coroutine with no
caller, which is exactly why the corpus was empty in production while every
test around it passed. A scratchpad script is not a fix.

Until the doctor exists, the land ritual on this repo carries a manual step:
after pushing master, run `pipx install --force ~/dev/axon`. The install is
deliberately a snapshot, not `--editable` - this checkout moves between
branches, and an editable install would let a feature branch silently become
what the MCP and all 13 repos' hooks execute.

## Phase 2 - the four already-ready issues

#97 (gitleaks flakiness), #94 (`dashboard.py` builds HTML via `innerHTML`),
#93 (optional auth on `serve-http` before `--host` widens), #92 (ADR inference
runs an LLM over untrusted commit messages).

    forge run --wip 1

Serial on purpose: three of the four are security-flagged, and `loop.yaml`
arms the classifier floor on `secrets`/`destructive-tools` keywords, so they
route to the Legendary tier and produce large diffs. One at a time keeps the
review surface honest.

#92 is the only one of the four with a real threat model today: commit messages
are attacker-influenced input in any repo that takes outside contributions.
#93 and #94 matter when, and only when, the service stops being localhost-only.

## Phase 3 - measurement, not implementation

#56 (is the recall prefix stable enough for the prompt cache?) and #57 (measure
`ask()` compression quality and latency) are **benchmarks**. Do not hand them
to `forge task`; there is nothing to implement until the numbers exist.

#56 is the same item as the "KV-stable SessionStart injection" conclusion from
the 2026-08-03 context-economy research: today `.axon/context.md` is rewritten
per session, which breaks the cached prefix. The issue predates that
conclusion - do not open a second one.

Run these under the METRON protocol (`~/dev/tools/metron/merit-graph-vs-loop/`
is the reference shape).

## Phase 4 - owner-only, do not delegate

#59 (errata sweep: external material still citing retired numbers) and #58
(rewrite the launch post drafts). These are public claims about measured
results. An agent rewriting them unsupervised is how a wrong number gets
republished. Owner writes, agent may only check citations against the snapshot.

## Environment notes (verified 2026-08-22)

- Embedding chain is `deepinfra` alone, set in `~/.zshrc`. The code default
  `ollama,deepinfra` fronts every embedding with a hop that cannot answer (no
  Ollama on this Mac by choice; the desktop needs Tailscale up). NIM is not the
  replacement: the key is valid and `baai/bge-m3` is listed among its 102
  models, but `/v1/embeddings` returns 500 for every payload shape, and the
  dead hop costs 0.72s per embedding against 0.82s for the call that works.
- The CLI is `axon`. `pb` does not exist any more; docs still referencing
  `pb doctor` / `pb adr review` / `pb hooks install` are stale.

## Gate and known traps (from `.claude/loop.yaml`)

- Gate: `ruff check src/axon/router src/axon/resilience tests/router tests/resilience && pytest tests/router tests/resilience tests/store tests/scripts tests/cli tests/doctor tests/config -q`
- Setup: `pip install -e ".[dev]"`
- Worktrees are safe here: the root `conftest.py` inserts the worktree-relative
  `src`, so per-worktree tests validate that worktree's code.
- `pytest` can hang on exit (unclosed asyncpg pool after the summary prints).
  Wrap gate runs in a timeout and read the printed "N passed/failed".
- A full `pytest -q` is RED on master from pre-existing test debt (benchmark,
  some hooks TTY/Windows cases). That is not a regression and not an env leak.
  Do not try to "fix" it as part of another task.

## Recommended order

Phase 1 -> Phase 2 -> Phase 3 -> Phase 4.

Phase 1 comes first because it is the only phase that protects the other three:
every phase below it writes code whose effect on the running system is
currently invisible until someone goes digging through pipx logs.
