# dec-111 - L1-L3 validation with tiers, density, and dormant draft pool

- Status: accepted
- Date: 2026-05-27

## Context

ADR inference via LLM (`src/axon/cli/pb.py:1439`) currently persists
directly to `SessionStore` with no validation gate. Red-team
R1 identified this as a "hallucinated ADR" vector: the LLM can generate
a syntactically valid ADR about a non-existent decision, and the vault
accepts it.

Successive iterations converged on deterministic, layered validation
rather than probabilistic (LLM confidence-score is not calibrated - R2),
using `diff ∪ commit_msg_body` to cover abstract ADRs (R3), with an
anti-boilerplate density gate (R4), L1 in two tiers to preserve hook SLA
(R4), and a structural detector to avoid false negatives on topological
refactors (R5).

## Decision

An inferred ADR passes through deterministic layers. Failure at any layer ->
dormant draft pool in `.axon/adr-draft/` (not indexed, recoverable),
not stored in the vault.

### Validation layers

| Layer | Runs in | Check | Pool considered |
|---|---|---|---|
| **L1-light** | hook (SLA <100ms) | `git cat-file` for files, `git grep` for symbols in the working tree after commit | working tree |
| **L1-full** | background / `pb adr review` | tree-sitter graph (via `axon.code`) | hot index |
| **L2 lexical** | hook | rationale × token overlap >= N (default 3 non-stopword) | `diff ∪ commit_msg_body`, after denylist |
| **L3 polarity** | hook | ADR key terms have grep-match | same |
| **Density** | hook | boilerplate denylist + architectural lexicon not-in-diff + overlap ratio cap 0.7 | same |
| **Structural detector** | hook | renames/moves relax density gates | n/a |
| **L4 human** | opt-in batch | `pb adr review` confirms drafts | n/a |

### L1 in two tiers

The hook **always** uses L1-light. Maximum contribution to hook: <100ms.

Background revalidates with L1-full on three deterministic triggers
(no daemon - dec-112 prohibits it):

1. `post-merge` / `post-checkout` hook (within `pb hooks install` scope)
2. Next `pb capture-*` or `pb adr infer-commit` (amortized)
3. `pb doctor` (manual)

Hard TTL of 24h: drafts without L1-full revalidation -> `stale-pending`
state, reported by doctor.

L1-full can **promote** (valid), **demote to dormant** (symbol does not
exist even in the hot index), or **keep as draft** (indeterminate).

### Density gate

Three combined checks, anti-boilerplate:

1. **Denylist**: tokens in a fixed set (`JIRA-*`, `#\d+`,
   `Co-authored-by`, `Signed-off-by`, conventional commit types)
   do not count toward overlap.
2. **Architectural lexicon hit**: rationale must contain >= 1 token from
   the lexicon (`migrate|replace|adopt|introduce|deprecate|refactor|
   pattern|layer|interface|contract|dependency|invariant|...`) that
   is **not** in the diff. Proves genuine commentary, not paraphrase.
3. **Overlap ratio cap**: rejected if >70% of the rationale's tokens are
   literal substrings of the diff (LLM copy-paste).

Initial lexicon: `axon/data/architectural_lexicon.txt`, ~30 terms.
Expandable via `axon.toml#adr.lexicon_path`.

### Structural detector

A commit is classified `structural` if any of:

- `git diff --find-renames=80% --name-status` reports >= 2 renames
- >= 3 new files in non-existing directories
- >= 2 directories renamed/moved
- Diff is >90% path changes

In structural mode, density gates relax:

| Gate | Default | Structural |
|---|---|---|
| `overlap_ratio_cap` | 0.7 | 0.9 |
| Architectural lexicon outside diff | mandatory | waived |
| L2 min overlap | 3 | 2 |

Audit log records `structural_mode: true` for post-hoc auditing.

### Draft pool

- Drafts at `.axon/adr-draft/{commit_hash}.md`
- After 30 days (configurable): marked `dormant`, excluded from default
  retrieval, recoverable via `pb adr review --dormant`
- **Do not expire destructively** - preserves institutional memory

### Observability

- Every rejection -> `.axon/adr-rejected.jsonl`:
  `{commit_hash, layer, reason, tokens_missing?, file_missing?,
  density_score?, structural_mode?, ts}`
- Passes with density below threshold (but above rejection) ->
  recorded as **weak-pass** in the same log
- `pb adr audit [--since=7d] [--weak-passes]` lists candidates
- Thresholds configurable in `axon.toml#adr.*`

### Hook SLA

<500ms p99 total (L1-light + L2 + L3 + density + write to
pending). Exceeded -> fallback: derived capture only, ADR goes to
pending without L1 validation, revalidated later by background.

## Rationale

- **Structural validation alone does not capture causal inversion** -
  needs lexical + polarity layers.
- **Pool `diff ∪ commit_msg_body`** - abstract ADRs have conceptual
  rationale whose diff is only imports; the commit body is a legitimate
  part of the architectural signal.
- **Density against boilerplate** - without it, a diff copy-paste in the
  body trivially passes L2/L3.
- **L1 in tiers preserves SLA** - the tree-sitter graph is expensive;
  git-only is fast and sufficient for the hook path. Final precision via
  background.
- **Structural detector** - topological refactors (renaming a directory
  to break coupling) have rationale ≈ diff by nature; rejecting them
  would be a critical false negative.
- **Dormant draft** - not expiring destructively preserves institutional
  memory against sprint pressure.

## Consequences

- New module `axon.adr.gates` with submodules `l1`, `l2`, `l3`,
  `density`, `structural`.
- New module `axon.adr.draft_pool` for write/dormancy in
  `.axon/adr-draft/`.
- New resource `axon/data/architectural_lexicon.txt`.
- `adr_infer_commit` (`pb.py:1439`) refactored to orchestrate:
  signal -> infer -> L1-light -> L2 -> L3 -> density -> structural -> draft
  pool or SessionStore.
- New CLIs:
  - `pb adr review [--dormant] [--weak-passes]`
  - `pb adr audit [--since=7d]`
  - `pb adr validate-drafts` (called by triggers)
- `post-merge` and `post-checkout` hooks added to the scope of
  `pb hooks install` ([dec-113](dec-113-hooks-pre-commit-framework.md)).
- `pb doctor` reports `stale-pending` drafts (TTL exceeded) -
  [dec-114](dec-114-doctor-diagnostic-first.md).
- Accepted as residual risk: hallucination that passes L1-light + L2/L3 +
  density (low probability); demoted by L1-full later.
- Accepted as residual risk: L1-full may demote a draft hours after the
  hook; user may read the vault between the two points.
- Accepted as residual risk: initial lexicon may reject valid ADRs -
  configurable, evolves from feedback.
