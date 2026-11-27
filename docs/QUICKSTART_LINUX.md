# Quickstart for Linux

Recommended mode: `full-local`

Linux is the strongest path for a full local AXON install: engine and
stores on the same machine.

## Before you start

Install these first:

- Git
- Python 3.11+
- `pipx`
- Docker Engine with Compose

No provider key is needed: AXON calls no LLM and no embedding provider
(dec-138).

## 1. Clone the engine

```bash
git clone <your-repo-url> ~/dev/axon
cd ~/dev/axon
```

## 2. Run setup

```bash
./setup.sh
pipx install --editable .
```

`setup.sh` writes `.env.local` and starts the local Postgres container (`docker compose up -d axon-postgres`). It pulls no model. The optional vault TIL hook needs Ollama on the host: install it from https://ollama.com and run `ollama pull gemma4:e4b`. If an older setup started the Ollama containers, they keep host port 11434 busy until you run `docker compose up -d --remove-orphans`; that removes them, while the downloaded models stay in `./data/ollama` until you delete that folder. Older `.env.local` files may still hold `OLLAMA_*`, `AXON_OLLAMA_*` and `LANGFUSE_HOST` lines that AXON no longer needs; you can delete them. If a local `docker-compose.override.yml` still names `ollama` or `ollama-cpu`, delete those entries before you run `setup.sh`; otherwise Compose rejects the project.

## 3. Load the environment

```bash
set -a
source .env.local
set +a

export AXON_ENGINE="$PWD"
export AXON_VAULT="$HOME/vault"
```

## 4. Create a small vault

```bash
mkdir -p \
  "$AXON_VAULT/knowledge" \
  "$AXON_VAULT/personal" \
  "$AXON_VAULT/career"

cat > "$AXON_VAULT/knowledge/first-note.md" <<'EOF'
# AXON

AXON indexes Markdown notes from an external vault.
EOF
```

## 5. Initialize a repo and check

```bash
axon init /path/to/your-repo
axon health
```

`axon health` should report `postgres: ok`. A commit whose subject starts with
`decision:` now shows up in `axon status`.

## Next

- Run `docker compose ps` when you want to confirm the local stack
- See [Vault setup](VAULT_SETUP.md) for the fuller vault layout
