#!/usr/bin/env bash
# axon-bootstrap — padrão de adoção de AXON em qualquer repo
#
# Uso:  scripts/axon-bootstrap.sh /path/to/repo [agent]
#
# O que faz:
#  1. Valida que o alvo é um repositório git limpo de hooks customizados.
#  2. Roda `axon init` (instala post-commit + pre-push e indexa o código).
#  3. Cria/atualiza `.claude/settings.json` com o MCP server do AXON.
#  4. Faz um health-check e imprime os próximos passos.
#
# Não toca em `.env.local` do repo alvo — backends ficam configurados
# globalmente em ~/.zshrc ou no .env.local do Prometheus.

set -euo pipefail

REPO="${1:-}"
AGENT="${2:-claude-code}"

if [[ -z "$REPO" || ! -d "$REPO/.git" ]]; then
  echo "uso: $0 <path-do-repo> [agent]" >&2
  echo "    repo precisa ser um diretório git válido" >&2
  exit 2
fi

REPO="$(cd "$REPO" && pwd -P)"
NAME="$(basename "$REPO")"

echo "→ alvo: $REPO (agent=$AGENT)"

# 1. Conflito de hooks?
for h in post-commit pre-push; do
  if [[ -f "$REPO/.git/hooks/$h" ]]; then
    if ! grep -q "axon" "$REPO/.git/hooks/$h" 2>/dev/null; then
      echo "✖ $h já existe em $REPO/.git/hooks/ e não é do AXON." >&2
      echo "  Inspecione antes — rode novamente após resolver." >&2
      exit 3
    fi
  fi
done

# 2. axon init (idempotente)
echo "→ axon init"
( cd "$REPO" && axon init . )

# 3. MCP em .claude/settings.json — merge não-destrutivo via jq.
SETTINGS="$REPO/.claude/settings.json"
mkdir -p "$REPO/.claude"
if ! command -v jq >/dev/null 2>&1; then
  echo "✖ jq não encontrado — necessário pra fazer merge seguro de settings.json" >&2
  exit 5
fi

AXON_ENTRY=$(jq -n --arg agent "$AGENT" '{
  axon: { command: "axon", args: ["serve"], env: { AXON_AGENT: $agent } }
}')

if [[ -f "$SETTINGS" ]]; then
  if jq -e '.mcpServers.axon' "$SETTINGS" >/dev/null 2>&1; then
    echo "→ MCP entry já presente em $SETTINGS"
  else
    cp "$SETTINGS" "$SETTINGS.bak.$(date +%Y%m%d-%H%M%S)"
    echo "→ backup: $SETTINGS.bak.*"
    jq --argjson entry "$AXON_ENTRY" \
       '. + {mcpServers: ((.mcpServers // {}) + $entry)}' \
       "$SETTINGS" > "$SETTINGS.tmp" && mv "$SETTINGS.tmp" "$SETTINGS"
    echo "→ MCP server mesclado em $SETTINGS (config existente preservada)"
  fi
else
  jq -n --argjson entry "$AXON_ENTRY" '{mcpServers: $entry}' > "$SETTINGS"
  echo "→ MCP server registrado em $SETTINGS"
fi

echo
echo "✓ AXON pronto em $NAME"
echo "  próximos passos:"
echo "   - confirme os backends:  axon health   (sqlite/redis/qdrant devem estar ok)"
echo "   - reinicie seu agent (Claude Code/Codex) pra carregar o MCP"
echo "   - faça um commit; depois rode 'axon status' pra ver a captura"
