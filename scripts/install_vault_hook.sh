#!/usr/bin/env bash

set -euo pipefail

VAULT_DIR="${AXON_VAULT:-$HOME/vault}"
HOOK_DIR="$VAULT_DIR/.git/hooks"
HOOK_PATH="$HOOK_DIR/post-commit"

mkdir -p "$HOOK_DIR"

cat > "$HOOK_PATH" <<'EOF'
#!/usr/bin/env bash

set -euo pipefail

echo "Sincronizando vault..."

if command -v pb >/dev/null 2>&1; then
  pb til --promote-today || true
else
  PYTHONPATH="$HOME/dev/Prometheus/src${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m prometheus.vault.til_promoter || true
fi

if git status --porcelain | grep -q "howto-"; then
  git add knowledge/
  git commit -m "auto: promote TILs to HOW-TOs [skip ci]" --quiet || true
fi

git pull --rebase origin main --quiet
git push origin main --quiet

echo "Vault sincronizado."
EOF

chmod +x "$HOOK_PATH"
echo "Hook instalado em $HOOK_PATH"
