# Quickstart for macOS

Recommended mode: `hybrid-local`

This is the fastest product-facing macOS path to a working `pb ask`: run the
engine on your Mac, let AXON provision the local CPU stack, and keep
cloud-routed calls available for heavier reasoning.

## Before you start

Install these first:

- Git
- Python 3.11+
- `pipx`
- Docker Desktop
- Ollama (optional — opt-in via `AXON_PROVIDER_OLLAMA=1`; skip on 16 GB Macs)

You also need API keys for the active provider profile (default is `free`):

- `GROQ_API_KEY` from <https://console.groq.com/keys>
- `NVIDIA_NIM_API_KEY` from <https://build.nvidia.com>

If you prefer paid Claude routing via OpenRouter, set
`AXON_PROVIDER_PROFILE=paid` and provide `OPENROUTER_API_KEY` instead of NIM.
See `docs/decisions/dec-106-routing-profiles.md`.

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

`setup.sh` creates `.env.local` for your Mac and starts the local CPU profile.

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

AXON keeps technical notes in an external vault and retrieves them with `pb ask`.
EOF
```

## 5. Index and ask

```bash
pb index "$AXON_VAULT/knowledge" --ctx knowledge
pb ask "What does this vault contain?"
```

If you get an answer back, the quickstart worked.

## Next

- Add more notes under `~/vault/knowledge`
- Index `personal` and `career` when you need them
- See [Vault setup](VAULT_SETUP.md) for the fuller vault layout
