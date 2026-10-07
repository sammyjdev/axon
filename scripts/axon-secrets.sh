#!/usr/bin/env bash
# Keep provider keys in a systemd-creds blob (encrypted, bound to this machine and user), not in exports.
# Usage: axon-secrets.sh set NAME | unset NAME | list | run -- COMMAND [ARGS...]
set -euo pipefail

STORE="${AXON_CREDENTIALS_FILE:-${XDG_CONFIG_HOME:-$HOME/.config}/axon/credentials.cred}"

load() {
  [ -f "$STORE" ] || return 0
  systemd-creds --user decrypt --name=axon "$STORE" -
}

save() {
  mkdir -p "$(dirname "$STORE")"
  rm -f "$STORE.tmp"
  systemd-creds --user encrypt --name=axon - "$STORE.tmp"
  chmod 600 "$STORE.tmp"
  mv "$STORE.tmp" "$STORE"
}

valid_name() {
  [[ "${1:-}" =~ ^[A-Z_][A-Z0-9_]*$ ]] || { echo "invalid variable name: ${1:-<empty>}" >&2; exit 2; }
}

# Decrypt before writing: a store that cannot be read must abort, never be replaced.
without() { load | { grep -v "^$1=" || true; }; }

case "${1:-}" in
  set)
    valid_name "${2:-}"
    [ -t 0 ] && printf 'Value for %s (hidden): ' "$2" >&2
    IFS= read -rs value
    [ -t 0 ] && echo >&2
    [ -n "$value" ] || { echo "empty value, nothing stored" >&2; exit 2; }
    kept="$(without "$2")"
    { [ -z "$kept" ] || printf '%s\n' "$kept"; printf '%s=%s\n' "$2" "$value"; } | save
    echo "stored $2 in $STORE" >&2
    ;;
  unset)
    valid_name "${2:-}"
    kept="$(without "$2")"
    { [ -z "$kept" ] || printf '%s\n' "$kept"; } | save
    ;;
  list)
    load | cut -d= -f1
    ;;
  run)
    shift
    [ "${1:-}" = "--" ] && shift
    [ $# -gt 0 ] || { echo "usage: $0 run -- COMMAND [ARGS...]" >&2; exit 2; }
    secrets="$(load)"
    while IFS='=' read -r name value; do
      [ -z "$name" ] || export "$name=$value"
    done <<<"$secrets"
    exec "$@"
    ;;
  *)
    echo "usage: $0 set NAME | unset NAME | list | run -- COMMAND [ARGS...]" >&2
    exit 2
    ;;
esac
