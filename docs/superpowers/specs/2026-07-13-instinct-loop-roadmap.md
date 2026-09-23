# Instinct Loop Roadmap (continuous learning)

**Date:** 2026-07-13
**Status:** Approved direction — pending distillation into backlog items / issues
**Provenance:** Gap analysis of the berserquir harness
(github.com/Beserq/berserquir, validated in code on 2026-07-13: instincts
protocol, `memory-journal.mjs`, claude-code `hook-adapter.mjs`, `/learn` +
`/evolve` prompts, eval e13). Sibling specs from the four-repo sweep:
`2026-07-13-retrieval-eval-precision-roadmap.md` (this repo),
`~/.claude/agents/forge/docs/loop-quality-roadmap.md`,
`gnomon-eval/docs/superpowers/specs/2026-07-13-ragas-completion-roadmap.md`,
`glyph-kg/docs/superpowers/specs/2026-07-13-code-provenance-and-depth-eval.md`.

## Validation outcome (2026-07-15, addendum)

Adversarially validated by two independent reviewers (Claude Fable:
SOUND-WITH-CHANGES; Codex gpt-5.6-sol: FLAWED). Combined verdict: direction
stands, mechanics rewritten. Binding changes:

- **IL-1 is a recurrence REPORT over the existing TraceStore** (policy-stage
  denies are already persisted by `traced_tool.py:190`), surfaced in
  `.axon/context.md` at session end. The original "persist a session note
  per deny" duplicated data and never reached recall (`axon_get_context`
  returns decisions only). Implemented 2026-07-16 as
  `observability/friction.py` + file_bridge section.
- **IL-2 leaves this roadmap**: it is onboarding UX, not learning, and the
  original premise was wrong (`pb hooks install` only manages git hooks).
  Zero-code replacement: a SessionStart hook snippet in
  `~/.claude/axon/ROUTER.md` that cats `.axon/context.md`.
- **IL-3 drops the confidence lifecycle** (it reinforces the wrong signal:
  recurrence after activation means the instinct fails, yet the arithmetic
  would boost it). Replacement: distinct-day counts + statuses
  candidate/active/disabled/promoted with HUMAN activation. dec-115's 0.93
  threshold is NOT reusable as-is (calibrated for decisions, default-off).
  Still gated: build only if the IL-1 report shows >=1 pattern with >=3
  distinct days.
- **IL-4 removed from the active roadmap** (no clusters can exist before IL-3).
- **New explicit item: human-corrections capture is a SEPARATE future
  source** (manual, via `axon_capture_event`), not transcript mining - the
  original goal named it as input but defined no capture path.
- **Self-measurement demoted to descriptive tracking**: a solo operator
  cannot generate the ~60-200 exposures per period a causal claim needs.
  Zero recurring patterns in the report is a VALID outcome - it decides the
  loop stays unbuilt, at the cost of ~100 lines.

## Goal

Close the behavioral-learning gap the berserquir analysis exposed: AXON
records decisions and facts but nothing in the ecosystem learns *recurring
behavioral patterns* (repeated guardrail friction, repeated human
corrections) with a confidence lifecycle and session-start injection. The
loop's only LLM stage is extraction/drafting — everything else is arithmetic
over data AXON already stores, which is what makes it worth having.

**Self-measuring rule (inherits the promotion rule).** The loop's
effectiveness metric is the recurrence rate of the same friction pattern
before vs after an instinct is active and injected. If the same deny /
correction keeps recurring with its instinct loaded, the loop does not work
and later stages do not land. No stage past IL-1/IL-2 ships without this
number.

## Item IL-1: Friction traces — denials into the recall path

**Problem.** dec-109 already emits a `ComplianceEvent` on every denied tool
call (`DENY_RESTRICTED_TOOL_WRITE`, `DENY_DESTRUCTIVE_NO_CONSENT`), but the
compliance log lives outside recall — nothing that plans future work ever
sees the walls the agent keeps hitting. Berserquir journals the guard
verdict itself and calls it the highest-signal learning input; the claim is
plausible and cheap to test.

**Direction.** At the deny path of `PolicyRegistry.decide_tool_action` (or
the `@traced_tool` middleware), additionally persist a session note through
the existing `axon_capture_event` semantics: event type `guard_deny`,
payload = tool, reason code, ctx, repo. `SessionNote` storage and
`get_session_memory` retrieval already exist — this is wiring, not a
subsystem. Effort: S. Ready.

## Item IL-2: SessionStart injection instead of ritual-carried recall

**Problem.** Recall at session start is ritual-carried (`~/.claude/axon/
ROUTER.md` instructs the agent to call `axon_get_context`) — the degraded
mode berserquir uses only for harnesses *without* session hooks. Claude Code
has native SessionStart hooks; AXON does not use them.

**Direction.** An opt-in SessionStart hook (installed via the existing
`pb hooks install` flow) that emits a compact `axon_get_context` result (or
the `.axon/context.md` mirror) to stdout for onboarded repos, token-budgeted.
Ritual stays as fallback for other harnesses. Effort: S. Ready.

## Deferred items (gated)

### IL-3: Instinct records — recurrence mechanics + extraction

The core of the loop: a record type with `statement` (one line, imperative,
project-specific), `scope`, `confidence`, `status`
(candidate/active/promoted/expired), `evidence` (≥1 ref, mandatory),
timestamps. Deterministic lifecycle arithmetic (born low, reinforced on
recurrence, decayed on contradiction, expired when stale); an extraction
pass over session notes + friction traces with berserquir's quality bar
(reject generic best practices, reject guardrail restatements — the instinct
is the *avoidance pattern*); injection = top-K active by confidence inside
the existing recall budget.

Two deliberate upgrades over the original: dedupe/reinforce matching uses
the dec-115 embedding near-dup machinery (cosine ≥ 0.93) instead of
prompt-trusted dedupe, and scope supports `repo` vs `global` (AXON is
multi-repo; berserquir's is repo-local). Do NOT import berserquir's
constants (0.3 / +0.2 / −0.3 / 30d) as validated — they are unvalidated
numerology from a five-day-old repo; pick initial values, flag them as
calibration knobs, and let the self-measuring rule judge. Effort: M.
**Trigger:** IL-1 has accumulated real friction data showing recurring
patterns (same reason code + tool shape ≥3 times across sessions). No
recurrence in the data = nothing to learn = the loop stays unbuilt.

### IL-4: Promotion — mature clusters into skills/ADRs

Clusters of ≥3 related active instincts promoted into a generated skill or
ADR draft, human-gated exactly like `pb adr review --promote` (draft →
explicit OK → vault), provenance preserved (`promoted`, `promotedTo`).
Berserquir gates promotion on a derived pass@3 eval — too weak for our
standard; if a promotion gate is needed, it is a GNOMON-grade harness or an
explicit human review, not pass@3. Effort: M. **Trigger:** IL-3 live AND at
least one real ≥3-instinct cluster exists.

## Rejections (do not revisit without new evidence)

- **Per-edit journal (PostToolUse hook on every Edit/Write).** dec-104
  (event-driven, not time-driven) stands: commits and session boundaries are
  where decisions crystallize; per-edit capture is noise we deliberately
  rejected. Friction traces (IL-1) are events, not polling — they fit
  dec-104.
- **Human-profile / mentorship calibration.** Solo expert operator; the
  anti-deskilling problem this solves does not exist here.
- **Multi-harness compilation (adapters).** 100% Claude Code; carrying
  Copilot/Cursor targets is dead weight. Revisit only if the stack is ever
  published for third parties.
- **pass@3 as a promotion gate.** Statistically weaker than everything else
  in the ecosystem (GNOMON bootstraps case-level CIs); never adopt it as a
  quality bar.

## Ordering

IL-1 and IL-2 are independent, ready, and S-sized — both are `forge task`
candidates. IL-3 waits on IL-1 *data*, not just IL-1 code. IL-4 waits on
IL-3 clusters. Nothing here blocks or reorders the PREC-*, L1/OPS/HTTP/MS/EMB
items.
