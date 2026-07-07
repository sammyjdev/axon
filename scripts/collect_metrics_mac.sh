#!/usr/bin/env bash
set -euo pipefail

# AXON — metrics snapshot (Mac client -> Desktop infra)
#
# Collects:
# - budgets from env
# - Qdrant points_count per collection (via Desktop IP)
#
# pb ask / pb cost compression removed — those commands are permanently cut (see dec-125).
#
# Usage:
#   ./scripts/collect_metrics_mac.sh --desktop-ip 192.168.112.1
#
# Notes:
# - Qdrant is queried on the Desktop.
# - Requires: bash, python3, curl.

DESKTOP_IP="${DESKTOP_IP:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --desktop-ip)
      DESKTOP_IP="${2:-}"; shift 2 ;;
    -h|--help)
      sed -n '1,120p' "$0"; exit 0 ;;
    *)
      echo "Unknown arg: $1" >&2
      echo "Run with --help for usage." >&2
      exit 2 ;;
  esac
done

if [[ -z "${DESKTOP_IP}" ]]; then
  echo "Missing --desktop-ip (e.g. 192.168.112.1)" >&2
  exit 2
fi

QDRANT_URL="${QDRANT_URL:-http://${DESKTOP_IP}:6333}"

echo "== AXON metrics snapshot =="
echo "ts_utc: $(python3 - <<'PY'
from datetime import datetime, timezone
print(datetime.now(timezone.utc).isoformat())
PY
)"
echo "desktop_ip: ${DESKTOP_IP}"
echo "qdrant_url: ${QDRANT_URL}"
echo

echo "== Budgets (env) =="
echo "AXON_DAILY_BUDGET=${AXON_DAILY_BUDGET:-<unset>}"
echo "AXON_OPUS_BUDGET=${AXON_OPUS_BUDGET:-<unset>}"
echo "AXON_RTK_MAX_TOKENS=${AXON_RTK_MAX_TOKENS:-<unset>}"
echo

echo "== Qdrant collection stats (points_count) =="
python3 - <<PY
import json, sys, urllib.request

base = "${QDRANT_URL}".rstrip("/")

def get(path: str):
    with urllib.request.urlopen(base + path, timeout=3) as r:
        return json.loads(r.read().decode("utf-8"))

try:
    cols = get("/collections").get("result", {}).get("collections", [])
except Exception as e:
    print(f"ERROR: failed to query Qdrant collections at {base}: {e}", file=sys.stderr)
    sys.exit(1)

names = [c.get("name") for c in cols if c.get("name")]
if not names:
    print("(no collections found)")
    sys.exit(0)

for name in sorted(names):
    try:
        info = get(f"/collections/{name}").get("result", {})
        points = info.get("points_count")
        vectors = (info.get("config", {}) or {}).get("params", {}).get("vectors", {})
        if isinstance(vectors, dict):
            size = vectors.get("size")
            dist = vectors.get("distance")
        else:
            size = getattr(vectors, "size", None)
            dist = getattr(vectors, "distance", None)
        print(f"- {name}: points_count={points} vector_size={size} distance={dist}")
    except Exception as e:
        print(f"- {name}: ERROR {e}")
PY
echo
