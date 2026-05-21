#!/usr/bin/env bash
set -euo pipefail

# Prometheus — metrics snapshot (Mac client -> Desktop infra)
#
# Collects:
# - pb ask latency (N runs)
# - budgets from env
# - compression telemetry summary (pb cost compression)
# - Qdrant points_count per collection (via Desktop IP)
#
# Usage:
#   ./scripts/collect_metrics_mac.sh --desktop-ip 192.168.112.1
#   ./scripts/collect_metrics_mac.sh --desktop-ip 192.168.112.1 --runs 7 --query "summarize my indexing pipeline"
#
# Notes:
# - Run on the Mac (where you execute pb). Qdrant is queried on the Desktop.
# - Requires: bash, python3, curl, pb installed and configured.

DESKTOP_IP="${DESKTOP_IP:-}"
RUNS="${RUNS:-5}"
QUERY="${QUERY:-test query for pb ask latency}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --desktop-ip)
      DESKTOP_IP="${2:-}"; shift 2 ;;
    --runs)
      RUNS="${2:-}"; shift 2 ;;
    --query)
      QUERY="${2:-}"; shift 2 ;;
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

echo "== Prometheus metrics snapshot =="
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

echo "== pb cost compression =="
if command -v pb >/dev/null 2>&1; then
  # This prints "Sem dados..." if you haven't run pb ask yet.
  pb cost compression || true
else
  echo "pb not found in PATH"
fi
echo

echo "== pb ask latency (runs=${RUNS}) =="
echo "query: ${QUERY}"
python3 - <<PY
import os, subprocess, statistics, time, sys

runs = int("${RUNS}")
query = "${QUERY}"

def run_once() -> float:
    start = time.perf_counter()
    # Suppress output; we only want wall-clock time.
    p = subprocess.run(
        ["pb", "ask", query],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        env=os.environ.copy(),
    )
    end = time.perf_counter()
    if p.returncode != 0:
        raise RuntimeError(f"pb ask failed with exit_code={p.returncode}")
    return end - start

times = []
failures = 0
for i in range(1, runs + 1):
    try:
        t = run_once()
        times.append(t)
        print(f"run_{i}: {t:.3f}s")
    except Exception as e:
        failures += 1
        print(f"run_{i}: ERROR {e}")

if not times:
    print("No successful runs; cannot compute summary.", file=sys.stderr)
    sys.exit(1)

times_sorted = sorted(times)
def pct(p: float) -> float:
    if len(times_sorted) == 1:
        return times_sorted[0]
    k = int(round((p/100) * (len(times_sorted) - 1)))
    return times_sorted[max(0, min(k, len(times_sorted) - 1))]

print("")
print("summary:")
print(f"- ok_runs: {len(times)}")
print(f"- failed_runs: {failures}")
print(f"- min_s: {min(times):.3f}")
print(f"- avg_s: {statistics.mean(times):.3f}")
print(f"- p50_s: {pct(50):.3f}")
print(f"- p95_s: {pct(95):.3f}")
print(f"- max_s: {max(times):.3f}")
PY

