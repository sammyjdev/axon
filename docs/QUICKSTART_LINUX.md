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
- Ollama (optional — opt-in via `AXON_PROVIDER_OLLAMA=1`)

You also need API keys for the active provider profile (default is `free`):

- `GROQ_API_KEY` from <https://console.groq.com/keys>
- `NVIDIA_NIM_API_KEY` from <https://build.nvidia.com>

For paid Claude routing, set `AXON_PROVIDER_PROFILE=paid` and provide
`OPENROUTER_API_KEY`. See `docs/decisions/dec-106-routing-profiles.md`.

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

export AXON_PROVIDER_PROFILE=free
export GROQ_API_KEY=<your-groq-key>
export NVIDIA_NIM_API_KEY=<your-nim-key>
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

## 5. Index and ask

```bash
pb index "$AXON_VAULT/knowledge" --ctx knowledge
pb ask "What does this vault contain?"
```

If you get an answer back, the quickstart worked.

## Next

- Run `docker compose ps` when you want to confirm the local stack
- Index `personal` and `career` when you need them
- See [Vault setup](VAULT_SETUP.md) for the fuller vault layout
