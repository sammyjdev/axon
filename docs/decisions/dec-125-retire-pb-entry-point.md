# dec-125 — Retire the `pb` entry point, finish the T6.3 CLI unification

- Status: accepted
- Date: 2026-07-06

## Context

dec-100 planned `CLI entry point: pb → axon` but only renamed the package/env
vars/DB file. T6.3 (2026-05-22) built the new `axon` entry point and
re-registered a subset of `pb.py`'s commands, deliberately cutting
`ask`/`index`/`watch`/`til`/`deep`/`expand`/`career`/`cost`. Everything else in
`pb.py` kept working only because a stray `pb` script (never declared in
`pyproject.toml`'s `[project.scripts]`, a leftover from the pre-dec-100
`prometheus-engine` install) survived on disk. `pb.py` kept gaining real
features after T6.3 — `hooks` (dec-113), `pending` (dec-112), the dec-111/114
doctor checks — that were never ported to `axon`, so the officially packaged
CLI silently fell behind the one people actually ran.

## Decision

- `axon` becomes the single, complete CLI. Every still-relevant `pb.py`
  command is re-registered onto `axon.__main__:app`: `hooks`, `pending`,
  `portability`, `configure`, `note`, `session-save`, `index-dev`, `setup`.
- `doctor`: pb.py's dec-111–114 diagnostic (`--apply`/`--ci`, capture/adr/
  toolchain checks) wins over axon's simpler RTK/caveman presence check;
  the RTK/caveman section is folded into the winning `doctor` as an
  additional report section.
- `init`: axon's own `init` (install hooks + index a repo) is unchanged;
  pb.py's `init` (env/config scaffold for a fresh install) is renamed to
  `axon bootstrap` to avoid the name collision.
- The permanently-cut T6.3 commands stay cut. Their source is deleted from
  `pb.py` (not left as dead code); the tests that existed solely to pin those
  commands' contracts are deleted alongside them (Task 6) — a test that only
  exercises permanently-removed dead code is itself dead code.
- The stray `pb` binary is removed from the pipx venv; nothing on this or any
  future machine should register a `pb` script again, since `pyproject.toml`
  never declares one.

## Consequences

- **`axon doctor`'s exit code changed.** Before this dec, `axon.__main__`'s
  `doctor` always exited 0 (pure presence/liveness report). Since pb.py's
  richer diagnostic wins, `axon doctor` now exits 2 on a FAIL check and 1 on
  a WARN (dec-114's severity gate), even when the folded-in RTK/caveman
  section is fully healthy. Intentional — this is the whole reason pb.py's
  doctor was chosen to win — but any future script treating `axon doctor` as
  an always-succeeds liveness probe needs `axon doctor --ci` (always exits 0,
  emits JSON) instead of the human-readable default.
- `scripts/collect_metrics_mac.sh` and `scripts/install_vault_hook.sh` lose
  the metrics/automation they ran through the now-permanently-cut `ask`/
  `cost`/`til` commands.
- Historical decision docs (dec-100, dec-110–114) still say `pb doctor` /
  `pb init` — left as-is; they describe decisions made when those were the
  live command names.
- `~/.claude/AXON.md` (a global dotfile outside this repo) still documents
  `pb ...` commands and needs a manual follow-up edit — out of scope for this
  repo's diff.
