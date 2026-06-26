# dec-113 - Hooks via pre-commit framework, opt-in with `--apply`

- Status: accepted
- Date: 2026-05-27

## Context

`src/axon/hooks/git_installer.py` currently writes directly to
`.git/hooks/post-commit` and `pre-push`, appending an AXON block between
markers `# >>> AXON git hook >>>` / `# <<< AXON git hook <<<`.
Hooks fail silently (`|| true`), respecting dec-104.

Red-team R1 questioned hooks as a contract (bypassable with
`--no-verify`). R2 proposed `core.hooksPath .axon/hooks` for isolation;
**rejected** because `core.hooksPath` is global to the repo and
**silently disables `.git/hooks/`** - it usurps husky and any tool
that writes to the default directory. That is not coexistence.

Converged on: AXON joins the existing hooks ecosystem as a citizen,
never as its owner.

## Decision

### Hard principles

- AXON **does not** mutate `git config core.hooksPath` under any circumstance
- AXON **does not** write to `.git/hooks/` by default
- Every hook installation is an explicit user gesture with `--apply`
- Diagnostics (`pb doctor`) are read-only by default
  ([dec-114](dec-114-doctor-diagnostic-first.md))

### `pb hooks install` (replaces `pb adr hook-install`)

Behavior:

1. Detects the present hooks toolchain:
   - `pre-commit` framework (`.pre-commit-config.yaml`)
   - `husky` (`.husky/` or `package.json#husky`)
   - None (`.git/hooks/` empty or only samples)
2. Shows dry-run: exactly what will be written, where
3. Requires `--apply` to mutate

### Integration by toolchain

| Toolchain detected | `--apply` behavior |
|---|---|
| `pre-commit` framework | Adds/updates AXON entry in `.pre-commit-config.yaml` |
| `husky` | Generates wrapper text for the dev to paste manually into `.husky/post-commit` |
| None | Suggests installing `pre-commit` or writes directly to `.git/hooks/` with double confirmation |

### Hooks in AXON's scope

| Hook | Event | Consumer |
|---|---|---|
| `post-commit` | capture `CodeChange` + `pb adr infer-commit` if signaled | dec-110, dec-111 |
| `pre-push` | final snapshot + draft sync | dec-111 |
| `post-merge` | revalidate drafts via L1-full | dec-111 |
| `post-checkout` | revalidate drafts via L1-full | dec-111 |

All fail silently. None block git.

### Migration from `pb adr hook-install`

- Becomes a deprecated alias for 1-2 releases
- Emits warning + redirects to `pb hooks install`
- Next major removes it

### Behavior in CI

`pb hooks install` in a non-interactive environment (no TTY):

- Exit 1 with a clear message
- Never mutates anything
- Doctor `--ci` mode (dec-114) reports hook status without fixing

## Rationale

- **Hooks are a shared contract** with the team's toolchain. Automatic
  mutation is classified as unsafe in any tool review.
- **`core.hooksPath` is destructive** - silently breaks husky,
  that is not coexistence.
- **Pre-commit framework already manages chaining** - AXON joins as
  an entry; the framework handles orchestration.
- **Explicit `--apply`** preserves trust in corporate / OSS-team
  environments.
- **Hooks fail silently** (dec-104) ensures that AXON problems
  never block the developer.

## Consequences

- `src/axon/hooks/git_installer.py` refactored:
  - Toolchain detector
  - Per-toolchain integration generators
  - Dry-run default; `--apply` required
- New module `axon.hooks.precommit_integration` for
  `.pre-commit-config.yaml`.
- New module `axon.hooks.husky_integration` (wrapper text).
- New CLI `pb hooks install` in `pb.py`.
- `pb adr hook-install` kept as an alias with `DeprecationWarning`
  for 2 releases.
- `post-merge` and `post-checkout` hooks added to the default set (for
  dec-111 L1-full triggers).
- Accepted as residual risk: dev with no hooks toolchain needs an
  explicit command for derived capture via hook - capture via MCP
  (dec-103) continues to be zero-friction for agents that speak MCP.
- Accepted as residual risk: husky users need to paste the wrapper
  manually - workaround documented.
