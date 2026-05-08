#!/usr/bin/env bash
# SessionStart hook — surfaces Prometheus context at session open

ENGINE="${PROMETHEUS_ENGINE:-$(git rev-parse --show-toplevel 2>/dev/null)}"
STATE_FILE="${ENGINE}/.session_state"

echo "=== Prometheus session ==="
echo "date: $(date '+%Y-%m-%d %H:%M')"

if [[ -f "$STATE_FILE" && -s "$STATE_FILE" ]]; then
  echo "ctx: $(cat "$STATE_FILE")"
else
  echo "ctx: none (use: pb session <ctx>)"
fi

# Recent TILs in vault (last 3 days)
VAULT="${PROMETHEUS_VAULT:-$HOME/vault}"
if [[ -d "$VAULT/knowledge/daily" ]]; then
  TIL_COUNT=$(find "$VAULT/knowledge/daily" -name "til-*.md" -mtime -3 2>/dev/null | wc -l | tr -d ' ')
  echo "tils (3d): ${TIL_COUNT}"
fi

# Compression telemetry summary
TELEMETRY="${ENGINE}/data/telemetry/compressions.jsonl"
if [[ -f "$TELEMETRY" ]]; then
  CALL_COUNT=$(wc -l < "$TELEMETRY" | tr -d ' ')
  echo "compression calls: ${CALL_COUNT}"
fi

echo ""
echo "pb ask|search|til|plan|adr|cost|memory"
echo "=========================="
