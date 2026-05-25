# axon.pet — visual companion (prototype)

**Status**: prototype on branch `feat/axon-pet`. Not on master, not in the
public `axon` CLI yet. Run with `python -m axon.pet`.

## What it is

A small terminal companion that mirrors your workspace activity in real time:

- One dendrite per active user shell (TTYs detected via `ps`, ghost-filtered
  by 30-min idle threshold).
- Each dendrite fires when its TTY has I/O — type in another tab, watch its
  dendrite pulse.
- Counters and recent-moments timeline pulled live from AXON's `data/axon.db`
  (ADRs) and `data/compression/stats.jsonl` (token savings, T-104 noise
  filtered out).
- HAPPY state flashes when a new ADR appears in the database.

## Run

```bash
python -m axon.pet
```

Resolves `AXON_ROOT` via env var, then by walking up looking for
`data/axon.db`, then falls back to `~/dev/Prometheus`.

Ctrl+C to exit. Restores cursor on exit.

## What's still prototype

- Path resolution: should use `axon.config.runtime.load_runtime_config()`
  instead of the ad-hoc walk-up.
- No tests. v0 needs TDD coverage for the state detector and the data
  aggregators (per CLAUDE.md: features need testable acceptance criteria
  before implementation).
- Not wired into `__main__.py` as `axon pet` — would need typer integration.
- Handoff signal is stubbed at 0 because the `sessions` table is empty in
  current production. Real handoff detection requires capturing
  agent-identity changes (see dec-103 wiring).
- Hardcoded refresh intervals: TTY poll 0.25s, shell rescan 3s, data
  refresh 10s. Should be configurable.
- Ghost threshold (30 min) is a heuristic that worked for one user's iTerm
  setup; needs validation across Warp, Ghostty, tmux-heavy workflows.

## What it relies on

- macOS `ps -axo pid,tty,user,command` (Linux equivalent untested).
- Truecolor terminal (24-bit ANSI). All modern terminals support this.
- Read access to `/dev/ttysXXX` for atime checks (always true for the owning
  user on macOS).
- Read access to `data/axon.db` and `data/compression/stats.jsonl`.

## Not in scope for v0

- Sound/notification.
- Persistent mood that decays across pet runs.
- Multiple pet instances coordinating.
- Pet-driven actions (the pet observes; it does not act).
