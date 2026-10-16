# Quickstart for Linux

Recommended mode: `full-local`

Linux is the strongest path for a full local AXON install: engine,
stores, and local models on the same machine.

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

On Linux, `setup.sh` starts the local stack and pulls the default Ollama
models. NVIDIA hosts can use the GPU profile automatically when available.

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
