# Praxis Agent Guide

This is the canonical agent context file for contributors working in this
repository. `AGENTS.md` points to this file.

## Project Overview

Praxis is a LangGraph task-orchestration engine exposed over the Model Context
Protocol (MCP). It turns a goal — or a structured Markdown spec — into an
ordered plan of subtasks, hands them out one at a time, records outcomes, and
checkpoints the session to SQLite so it survives a process restart.

A coding agent drives Praxis through seven MCP tools served by `praxis-server`.

## Entry Points

- [README.md](README.md): public project overview and quick start
- [src/praxis/server.py](src/praxis/server.py): the seven MCP tools
- [src/praxis/graph.py](src/praxis/graph.py): the action-routed `StateGraph`
- [examples/spring-migration.md](examples/spring-migration.md): a worked spec

## Stable Architectural Decisions

### D1: Single-step, action-routed graph

- Every MCP call routes on the `action` channel, runs exactly one node
  (`plan` / `get_next` / `record` / `replan`), and ends.
- The graph is not a long-running loop. Continuity comes from the checkpointer,
  not from in-graph control flow.

### D2: Checkpoints stay plain JSON

- Every state type (`TaskState`, `Subtask`, `History`) round-trips through
  `to_dict` / `from_dict`.
- A reopened database must yield an identical `TaskState`. Do not put
  non-serializable objects on the graph channels.

### D3: SQLite is the persistence boundary

- Resumability is defined as reopening the same DB file in a fresh process.
- Any change to `state.py` or `checkpoint.py` must keep restart-resume intact.

### D4: Legacy Prometheus code is parked, not removed

- `src/prometheus`, `src/embedder`, `scripts`, and `docker-compose.prometheus.yml`
  remain on disk and are excluded from Praxis ruff / mypy / pytest scope.
- Do not revive parked code or mix it into the `praxis` package.

### D5: Per-task git worktrees

- Each task is isolated on its own worktree and `praxis/<id>` branch.
- `WorktreeManager` cleanup must leave nothing behind (worktree, branch, prune).

## Code Conventions

- Python 3.11+ with full type hints (`mypy` runs with `disallow_untyped_defs`).
- `from __future__ import annotations` at the top of every module.
- Prefer `dataclass` over ad-hoc dicts.
- Prefer async for I/O-heavy paths.
- Add comments only for non-obvious constraints or rationale.
- Keep public examples and docs machine-agnostic.

## Agent Rules

- Start from tests when changing behavior.
- Bugfixes should begin with a regression test when feasible.
- Features should have testable acceptance criteria before implementation.
- Do not silence failing tests or guardrails to make a change appear complete.
- Prefer the smallest coherent change that satisfies the behavior.

## Safety Rules

- Never commit credentials, tokens, `.env` files, or user data.
- Never weaken the `tests/praxis` acceptance suite as a shortcut.
- Investigate failing tests, hooks, or checks instead of bypassing them.

## Validation Defaults

`tests/praxis` is the active Praxis suite. Use `rtk` where available:

```bash
rtk pytest tests/praxis -q
rtk ruff check
rtk mypy src/praxis
```

## RTK Notes

Praxis is commonly used with RTK (Rust Token Killer) for compact command
output. Prefix commands with `rtk` when possible; if no specialized filter is
available, RTK passes the command through unchanged.
