#!/usr/bin/env bash
# axon-adopt-all — roda axon-bootstrap.sh em cada repo git filho de um diretório.
#
# Uso:  scripts/axon-adopt-all.sh [parent-dir] [agent]
#       default: parent-dir = ~/dev, agent = claude-code
#
# Para cada subdiretório com .git, tenta o bootstrap. Repos com hooks de
# terceiros (husky/lefthook/etc) são pulados (o próprio bootstrap aborta com
# exit 3). No final imprime resumo: adopted / already / skipped-conflict / error.

set -uo pipefail

PARENT="${1:-$HOME/dev}"
AGENT="${2:-claude-code}"

HERE="$(cd "$(dirname "$0")" && pwd -P)"
BOOTSTRAP="$HERE/axon-bootstrap.sh"

if [[ ! -x "$BOOTSTRAP" ]]; then
  echo "✖ $BOOTSTRAP não encontrado ou não executável" >&2
  exit 2
fi

adopted=()
already=()
skipped=()
errored=()

for d in "$PARENT"/*/; do
  [[ -d "$d/.git" ]] || continue
  name="$(basename "$d")"
  printf '\n=== %s ===\n' "$name"

  out=$("$BOOTSTRAP" "$d" "$AGENT" 2>&1)
  rc=$?
  echo "$out"

  case "$rc" in
    0)
      if grep -q "hooks installed: post-commit, pre-push" <<<"$out"; then
        adopted+=("$name")
      else
        already+=("$name")
      fi
      ;;
    3) skipped+=("$name") ;;
    *) errored+=("$name (rc=$rc)") ;;
  esac
done

printf '\n\n=== resumo ===\n'
printf 'adopted (novo)  : %s\n' "${adopted[*]:-(nenhum)}"
printf 'already (idem)  : %s\n' "${already[*]:-(nenhum)}"
printf 'skipped (hooks) : %s\n' "${skipped[*]:-(nenhum)}"
printf 'errored         : %s\n' "${errored[*]:-(nenhum)}"
