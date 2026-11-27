# AXON roadmap

Status: current plan, 2026-11-26. It replaces the earlier roadmap and TASKS.md, both kept in the git history.

Direction: governed memory for coding agents. Identity separation enforced at capture and closed by default; offline provenance from a line to its commit to the agent session, with no transcript sent to a remote; a record of what enters the context, with retention, forget and export. Storage moves to an embedded local database by default, with Postgres optional.

Every item goes through a reviewed plan before it is built. No date is promised. Open work is tracked in GitHub issues as items enter execution.

## F0 Credibility
- The spool never swallows a filesystem error and refuses when a payload could be staged (#202).
- `axon hooks install --apply` in pre-commit repos installs the drivers and registers the repo, or fails (#200).
- One `session_memory` row per session (#187).
- Privacy closed by default: with no identity configured, capture refuses until `axon init` creates one.
- Repo hygiene: only current measured claims, one roadmap (#59).

## F1 Must-haves (v0.2)
- Embedded storage by default (SQLite with full-text search), Postgres optional (#217).
- Native Windows, with Windows and macOS in CI.
- One-command install from PyPI.
- Optional local semantic search for lessons and conversations, shipped only if measured recall improves.

## F2 Governed memory (v0.3)
- Identity policy file (client, employer, personal), one store per identity, enforced fail-closed.
- Injection log and `axon audit`.
- Retention, `axon forget` and `axon export` per identity and repo.
- `axon why` per line.
- Compaction rescue.

## F3 Proof and launch
- An open, pre-registered coding-memory benchmark.
- README and site rebuilt with measured numbers; v1.0 decision.
