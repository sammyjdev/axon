# Vault Setup

This guide bootstraps the external Markdown vault used by Prometheus. The vault
is your data layer. This repository is only the engine.

## Before You Start

Make sure the engine is already installed and the local stack is up:

```bash
cd /path/to/prometheus

pb --help
docker compose ps
```

Set these environment variables to match your machine:

- `AXON_ENGINE=/path/to/prometheus`
- `AXON_VAULT=~/vault`

## 1. Create the Vault Layout

```bash
VAULT_ROOT=~/vault

mkdir -p \
  "$VAULT_ROOT/knowledge/daily" \
  "$VAULT_ROOT/knowledge/deep" \
  "$VAULT_ROOT/career" \
  "$VAULT_ROOT/personal" \
  "$VAULT_ROOT/work" \
  "$VAULT_ROOT/adrs"
```

Recommended meaning of each top-level folder:

| Folder | Purpose |
| --- | --- |
| `knowledge/` | technical notes, HOW-TOs, references |
| `career/` | interview prep, role research, goals |
| `personal/` | side projects, planning, decisions |
| `work/` | restricted professional context |
| `adrs/` | architecture decisions that are not project-local |

## 2. Add an Optional Vault-Level Agent File

If you use agentic tooling inside the vault, add a top-level `CLAUDE.md` with
your own operating rules.

Minimal example:

```markdown
# Vault Rules

- Read the current project context before editing.
- Do not access `work/` unless the user explicitly asks for it.
- Keep technical notes concise and searchable.
- Record architecture decisions as ADRs when they affect future work.
```

## 3. Mark the Restricted `work` Context

Create the `.ctxguard` marker inside `work/`:

```bash
printf "context=work\n" > ~/vault/work/.ctxguard
```

This marker is not the only protection layer, but it makes the boundary
explicit for humans and tooling.

## 4. Seed the First Index

Run initial indexing per public context:

```bash
pb index ~/vault/knowledge --ctx knowledge
pb index ~/vault/career --ctx career
pb index ~/vault/personal --ctx personal
```

Only index `work` when you explicitly intend to make that context searchable:

```bash
pb index ~/vault/work --ctx work
```

## 5. Validate Retrieval

Sanity-check the vault with a simple search and query:

```bash
pb search "test" --ctx knowledge --top 3
pb ask "How is the vault organized?"
```

Expected outcome:

- `pb search` completes without store errors
- `pb ask` reports a detected context and returns prompt-ready output

## 6. Optional: Install the Vault Git Hook

The repository ships a helper script for the vault post-commit hook:

```bash
bash "$AXON_ENGINE/scripts/install_vault_hook.sh"
```

This is useful if you want post-commit automation around vault workflows.

## 7. Optional: Run a Watcher

For continuous indexing while you edit notes:

```bash
pb watch ~/vault/knowledge --ctx knowledge
```

Run separate watcher sessions only when you actually need them. Manual indexing
is often enough for smaller vaults.

## 8. Obsidian Integration

If you use Obsidian:

1. Open `~/vault` as a vault.
2. Keep folder names stable after initial indexing.
3. Reindex after large structural moves if you are not running `pb watch`.

## Troubleshooting

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| `pb search` returns no hits | no indexed content yet | add notes, then rerun `pb index` |
| `Path not found` during indexing | wrong vault path | verify `AXON_VAULT` and the target path |
| `work` access asks for confirmation | expected behavior | pass `--ctx work` only when intended |
| `pb ask` fails after setup | env not loaded in current shell | `set -a; source .env.local; set +a` |

## Next Step

Continue with the [usage guide](USAGE_GUIDE.md) for common CLI workflows,
retrieval patterns, and ongoing operations.
