# Quickstart for Windows with WSL2

Recommended mode: `remote-infra`

Windows support goes through WSL2. Run the CLI inside a WSL2 Linux shell and
keep the heavy services on another machine.

## Before you start

Inside WSL2, install these first:

- Git
- Python 3.11+
- `pipx`
- `curl`

You also need:

- a reachable remote Prometheus infra host with Qdrant, Redis, Neo4j, Langfuse,
  and Ollama running
- an `ANTHROPIC_API_KEY` for cloud-routed calls

## 1. Open WSL2 and clone the engine

```bash
git clone <your-repo-url> ~/dev/Prometheus
cd ~/dev/Prometheus
```

## 2. Point setup to the remote infra host

```bash
export PROMETHEUS_INFRA_HOST=<remote-host-or-ip>
./setup.sh
pipx install --editable .
```

In this mode, `setup.sh` writes remote service URLs into `.env.local` and skips
local Docker startup and local Ollama model pulls.

## 3. Load the environment

```bash
set -a
source .env.local
set +a

export ANTHROPIC_API_KEY=<your-key>
export PROMETHEUS_ENGINE="$PWD"
export PROMETHEUS_VAULT="$HOME/vault"
```

## 4. Create a small vault

```bash
mkdir -p \
  "$PROMETHEUS_VAULT/knowledge" \
  "$PROMETHEUS_VAULT/personal" \
  "$PROMETHEUS_VAULT/career"

cat > "$PROMETHEUS_VAULT/knowledge/first-note.md" <<'EOF'
# Prometheus

Prometheus can run locally in WSL2 while retrieval infra lives on another host.
EOF
```

## 5. Index and ask

```bash
pb index "$PROMETHEUS_VAULT/knowledge" --ctx knowledge
pb ask "What does this vault contain?"
```

If you get an answer back, the quickstart worked.

## Next

- Keep your repo, shell, and vault paths inside WSL2 instead of splitting them
  across Windows and Linux paths
- If you do not have a remote host yet, use the `minimal` validation path from
  [Support matrix](SUPPORT_MATRIX.md) while infra is being prepared
- See [Vault setup](VAULT_SETUP.md) for the fuller vault layout
