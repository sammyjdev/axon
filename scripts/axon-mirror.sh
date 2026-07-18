#!/usr/bin/env bash
# One-way mirror: local AXON Postgres (source of truth) -> cloud replica.
# The mirror is a backup and a future read endpoint for cloud agents.
# Nothing ever syncs back; see docs/decisions for the cloud-arm design.
#
# Usage: AXON_MIRROR_PG_URL=postgres://... scripts/axon-mirror.sh [--dry-run]
#
# Install as a daily launchd job:
#   1. Copy scripts/com.axon.mirror.plist.example to ~/Library/LaunchAgents/
#      and replace REPLACE_WITH/ and REPLACE_WITH_NEON_URL.
#   2. launchctl load ~/Library/LaunchAgents/com.axon.mirror.plist
#
# launchd only fires when the Mac is awake, so scheduling for 20:00 mirrors
# after a work day without keeping the machine on.
set -euo pipefail

SRC="${AXON_PG_URL:-postgresql://axon:axon@localhost:5433/axon}"
DST="${AXON_MIRROR_PG_URL:-}"

if [ -z "$DST" ]; then
  echo "error: AXON_MIRROR_PG_URL is not set" >&2
  exit 1
fi
case "$DST" in
  *localhost*|*127.0.0.1*|*'[::1]'*|*0.0.0.0*)
    echo "error: mirror target looks local - refusing to mirror onto itself" >&2
    exit 1
    ;;
esac

DUMP_ARGS=(--format=custom --no-owner --no-privileges)

if [ "${1:-}" = "--dry-run" ]; then
  echo "pg_dump ${DUMP_ARGS[*]} \$AXON_PG_URL | pg_restore --clean --if-exists --no-owner --dbname=\$AXON_MIRROR_PG_URL"
  exit 0
fi

pg_dump "${DUMP_ARGS[@]}" "$SRC" \
  | pg_restore --clean --if-exists --no-owner --dbname="$DST"
echo "mirror complete: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
