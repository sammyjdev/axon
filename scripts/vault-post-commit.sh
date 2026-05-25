#!/usr/bin/env bash
# ~/vault/.git/hooks/post-commit
#
# Instale com:
#   cp scripts/vault-post-commit.sh ~/vault/.git/hooks/post-commit
#   chmod +x ~/vault/.git/hooks/post-commit
#
# Ou via pb:
#   pb vault install-hooks

set -euo pipefail

AXON_ENGINE="${AXON_ENGINE:-$HOME/dev/axon}"

echo "Sincronizando vault..."

# Promoção de TILs do dia (roda local antes de subir)
if python3 "$AXON_ENGINE/src/axon/vault/til_promoter.py" 2>/dev/null; then
    # Se promoter criou novos HOW-TOs, adiciona ao commit
    NEW_FILES=$(git status --porcelain | grep -c "howto-" || true)
    if [ "$NEW_FILES" -gt 0 ]; then
        git add knowledge/
        git commit -m "auto: promote TILs to HOW-TOs [skip ci]" --quiet --no-gpg-sign
    fi
fi

# Sync com GitHub (só se remote configurado)
if git remote get-url origin &>/dev/null; then
    git pull --rebase origin main --quiet || true
    git push origin main --quiet || true
fi

echo "Vault sincronizado."
