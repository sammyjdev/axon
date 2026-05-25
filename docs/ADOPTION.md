# Adopting AXON in a new repo

This is the canonical recipe for installing AXON in any project so context
capture, recall, and handoff start working immediately.

## Prerequisites (once per machine)

1. AXON CLI installed and on `$PATH`:
   ```bash
   pipx install -e /path/to/Prometheus
   ```
2. Backends running locally:
   ```bash
   cd /path/to/Prometheus && docker compose up -d qdrant redis
   ```
3. `axon health` returns all subsystems `ok` (sqlite, redis, qdrant, mem0,
   vault, git). If `redis`/`qdrant` report `down (timeout)`, check that
   `QDRANT_URL` / `REDIS_URL` in your shell point to a reachable host.

## Per-repo bootstrap

```bash
/path/to/Prometheus/scripts/axon-bootstrap.sh /path/to/your-repo [agent]
```

`agent` defaults to `claude-code`. The script is idempotent.

It:

- aborts if the repo already has a non-AXON `post-commit` / `pre-push` hook
  (so you can resolve husky / lefthook / project-specific hooks first);
- runs `axon init .` (installs the two hooks and indexes the code graph);
- creates or updates `.claude/settings.json` so Claude Code auto-loads the
  AXON MCP server next time it starts;
- runs `axon health` and stops if any backend is degraded.

After the script exits clean, restart your coding agent and make a commit —
`axon status` should list the captured decision.

## What lives where

| Concern | Location |
|---|---|
| AXON code, hooks, indexer | Prometheus repo (engine) |
| Backends (Qdrant, Redis) | `docker compose` in Prometheus repo |
| Per-repo capture / hooks | `.git/hooks/post-commit`, `.git/hooks/pre-push` |
| Agent MCP registration | `.claude/settings.json` in target repo |
| Vault (optional, for ADR export) | `$AXON_VAULT` (defaults to `~/vault`) |

## Removing AXON from a repo

```bash
cd /path/to/your-repo
axon install-hooks --remove
rm .claude/settings.json   # only if it only contained AXON
```

The SQLite store at `/path/to/Prometheus/data/axon.db` retains captured
decisions across all repos; remove that file to wipe global memory.
