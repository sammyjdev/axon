# Adopting AXON in a new repo

This is the canonical recipe for installing AXON in any project so context
capture, recall, and handoff start working immediately.

## Prerequisites (once per machine)

1. AXON CLI installed and on `$PATH`:
   ```bash
   pipx install -e /path/to/axon
   ```
2. Backend running locally (single Postgres instance, `pgvector` for vectors —
   dec-121 retired Qdrant, Redis and mem0):
   ```bash
   cd /path/to/axon && docker compose up -d axon-postgres
   ```
3. `axon health` returns `ok` for `sqlite`, `pgvector`, `vault` and `git`. If
   `pgvector` reports `down (timeout)`, check `AXON_PG_URL` — it defaults to
   port 5433, but a `docker-compose.override.yml` can remap it on a machine
   with a port collision.

## Per-repo bootstrap

```bash
/path/to/axon/scripts/axon-bootstrap.sh /path/to/your-repo [agent]
```

`agent` defaults to `claude-code`. The script is idempotent.

It:

- aborts if the repo already has a non-AXON `post-commit` / `pre-push` hook
  (so you can resolve husky / lefthook / project-specific hooks first);
- runs `axon init .` (installs the two hooks and indexes the code graph);
- creates or updates `.claude/settings.json` so Claude Code auto-loads the
  AXON MCP server next time it starts.

After the script exits clean, restart your coding agent and make a commit —
`axon status` should list the captured decision.

## What lives where

| Concern | Location |
|---|---|
| AXON code, hooks, indexer | axon repo (engine) |
| Backend (Postgres + pgvector) | `docker compose` in axon repo |
| Per-repo capture / hooks | `.git/hooks/post-commit`, `.git/hooks/pre-push` |
| Agent MCP registration | `.claude/settings.json` in target repo |
| Vault (optional, for ADR export) | `$AXON_VAULT` (defaults to `~/vault`) |

## Installing AXON hooks in a repo

```bash
cd /path/to/your-repo
axon hooks install            # dry-run preview (dec-113)
axon hooks install --apply    # mutate; opt-in, TTY required
```

## Removing AXON from a repo

Open `.git/hooks/post-commit` (and `pre-push` / `post-merge` /
`post-checkout` if present) and delete the block between
`# >>> AXON git hook >>>` and `# <<< AXON git hook <<<`.

```bash
cd /path/to/your-repo
rm .claude/settings.json   # only if it only contained AXON
```

The Postgres store (single instance, dec-121) retains captured decisions
across all repos; there is no per-file store to delete — drop the `axon`
database, or the `axon-postgres` container's volume, to wipe global memory.
