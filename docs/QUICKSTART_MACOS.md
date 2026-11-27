# Quickstart for macOS

Recommended mode: `hybrid-local`

This is the fastest product-facing macOS path to a working AXON: run the
engine on your Mac and let AXON provision its local stack.

## Before you start

Install these first:

- Git
- Python 3.11+
- `pipx`
- Docker Desktop

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

`setup.sh` creates `.env.local` for your Mac and starts the local CPU profile.

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

AXON keeps its vault outside the engine repository.
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

- Add more notes under `~/vault/knowledge`
- See [Vault setup](VAULT_SETUP.md) for the fuller vault layout
