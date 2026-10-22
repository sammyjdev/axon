# Quickstart for Windows with WSL2

Recommended mode: `remote-infra`

Windows support goes through WSL2. Run the CLI inside a WSL2 Linux shell and
keep Postgres on another machine.

## Before you start

Inside WSL2, install these first:

- Git
- Python 3.11+
- `pipx`
- `curl`

You also need a reachable host running the `axon-postgres` service (Postgres
on port 5433). No provider key is needed: no commit and no MCP tool calls an LLM or an
embedding provider (dec-138).

## 1. Open WSL2 and clone the engine

```bash
git clone <your-repo-url> ~/dev/axon
cd ~/dev/axon
```

## 2. Point setup to the remote host

```bash
export AXON_INFRA_HOST=<remote-host-or-ip>
./setup.sh
pipx install --editable .
```

In this mode `setup.sh` checks Postgres on `$AXON_INFRA_HOST:5433`, writes
`.env.local` and skips the local Docker stack. It also probes Ollama on port
11434 of that host; AXON no longer uses it, so a warning there is harmless.

## 3. Load the environment

```bash
set -a
source .env.local
set +a

export AXON_ENGINE="$PWD"
export AXON_VAULT="$HOME/vault"
```

`.env.local` starts from `.env.example`, whose `AXON_PG_URL` points at
`localhost`. Edit it to the remote host, and set `AXON_LESSONS_PG_URL` the same
way: lessons live in their own database.

## 4. Create a small vault

```bash
mkdir -p \
  "$AXON_VAULT/knowledge" \
  "$AXON_VAULT/personal" \
  "$AXON_VAULT/career"

cat > "$AXON_VAULT/knowledge/first-note.md" <<'NOTE'
# AXON

AXON keeps its vault outside the engine repository.
NOTE
```

## 5. Initialize a repo and check

```bash
axon init /path/to/your-repo
axon health
```

`axon health` should report `postgres: ok`. A commit whose subject starts with
`decision:` now shows up in `axon status`.

## Next

- Keep your repo, shell and vault paths inside WSL2 instead of splitting them
  across Windows and Linux paths
- Register the MCP server for your agent: see [SETUP](../SETUP.md)
- See [Vault setup](VAULT_SETUP.md) for the fuller vault layout
