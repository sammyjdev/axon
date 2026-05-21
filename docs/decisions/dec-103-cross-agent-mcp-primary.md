# dec-103 — Cross-agent transport: MCP primary, file-based fallback

- Status: accepted
- Date: 2026-05-21

## Context

AXON must deliver the same context to multiple coding agents (Claude Code,
Codex, Cursor). Agents differ in protocol support: Claude Code speaks MCP;
others may not have MCP wired initially.

## Decision

- MCP (stdio) is the primary cross-agent transport. Context retrieval, capture,
  and handoff are exposed as MCP tools.
- A file-based fallback (`.axon/context.md` in the repo) mirrors the current
  context for agents without MCP. It is updated on session end and git events.

## Rationale

- MCP gives structured, on-demand retrieval and is already a dependency.
- The file fallback guarantees a baseline of continuity for any agent that can
  read a repo file, with zero integration cost.

## Consequences

- Two write paths for context state must stay consistent (Phase 3, T3.4).
- `.axon/` is a new repo-local directory; it must be safe to either commit or
  gitignore.
