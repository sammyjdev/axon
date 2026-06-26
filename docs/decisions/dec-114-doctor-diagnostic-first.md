# dec-114 - `pb doctor` diagnostic-first + toolchain validation

- Status: accepted
- Date: 2026-05-27

## Context

`pb doctor` (`src/axon/cli/pb.py:685`) currently has a single check with no
modes. ARD-010 already defines hardware-fit detection. P0-T4 of the roadmap
asks `pb doctor` to emit pass/warn/fail and recommend an operating mode.

Red-team R1 proposed `pb doctor --repair` to fix divergent hooks.
**Rejected** because automatic mutation of `.git/hooks/` or other shared
surfaces is classified as a risk in any enterprise tool review. R3 requested
validation of compatibility with the commit toolchain (`commitlint`,
`semantic-release`) for the `arch:` signal ([dec-110](dec-110-declarative-memory-signal.md)).
R4-R5 requested checks for `pending/` backlog, `stale-pending` drafts,
and the size of `pending-quarantine/`.

## Decision

### Three modes

| Mode | Default? | Behavior |
|---|---|---|
| `pb doctor` | yes | read-only diagnostics, exit code reflects severity |
| `pb doctor --apply` | opt-in | suggests fixes with interactive confirmation; **never** in CI |
| `pb doctor --ci` | explicit | structured JSON on stdout, exit 0 always |

`--apply` requires a TTY check; refuses with exit 1 in non-interactive mode.

### Required checks

| Check | Category | Detail |
|---|---|---|
| Hooks divergent from expected | hooks | without repairing (dec-113) |
| Runtime dependencies | env | Python >= 3.11, SQLite WAL viability |
| Hardware fit | env | reuses ARD-010 |
| Backlog in `.axon/pending/` | capture | warning if > N files or > T days |
| Drafts in `stale-pending` (TTL exceeded) | ADR | drafts without L1-full after 24h |
| Size of `.axon/pending-quarantine/` | capture | warning + listing |
| Persistent warnings in `capture-warnings.jsonl` | capture | signal of chronic contention |
| `arch:` compatibility with commit toolchain | hooks | scan `commitlint.config.*`, `.commitlintrc*`, `release.config.js`, `package.json#commitlint`; warning + fix snippet if strict `type-enum` without `arch`/`decision` |

### What doctor does NOT do

- Does not mutate the user's husky or pre-commit
- Does not fix `commitlint.config` automatically - only suggests
- Does not reinstall AXON hooks automatically - only reports divergence
- Does not delete dormant drafts - only reports accumulation
- Does not delete quarantine - only reports

### Output

**Default mode**: human-readable table with columns
`check | status | detail | suggestion`. Exit code reflects maximum severity
(0=ok, 1=warn, 2=fail).

**CI mode**: structured JSON:

```json
{
  "version": "1",
  "ts": "...",
  "checks": [
    {"name": "...", "status": "ok|warn|fail", "detail": "...",
     "suggestion": "..."}
  ],
  "summary": {"ok": N, "warn": N, "fail": N}
}
```

Exit 0 always in `--ci` to avoid breaking pipelines.

**Apply mode**: for each check with an available `auto_fix` (rare), interactive
prompt `[y/N]`. Without auto_fix -> same output as default.

## Rationale

- **Mutating doctor is a risk** in any enterprise security review.
  Diagnostics + opt-in `--apply` preserves value without crossing the line.
- **CI mode exit 0** prevents doctor from becoming a pipeline blocker due to
  warnings; the user decides when to act.
- **Commit toolchain validation** prevents production failures: dev configures
  `arch:`, type-enum rejects it, pipeline breaks. Doctor detects this before
  first use.
- **Backlog/quarantine checks** give visibility into capture state without
  manual inspection of `.axon/`.

## Consequences

- `pb doctor` refactored in `pb.py:685` for 3 modes.
- New module `axon.doctor` with `checks/` per category
  (hooks, env, capture, adr, toolchain).
- Each check exposes `(status, detail, suggestion, auto_fix?)`.
- Separate output formatters (`formatters/human.py`,
  `formatters/json.py`).
- `--ci` mode used by CI workflows (referenced in dec-107).
- Accepted as residual risk: user ignores persistent warnings -
  doctor does not force action, only reports.
- Accepted as residual risk: custom commit toolchain that does not
  follow `commitlint`/`semantic-release` convention is not detected -
  manual workaround via trailer (dec-110).
