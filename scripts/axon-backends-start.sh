#!/bin/bash
# axon-backends-start — espera o docker daemon ficar pronto e sobe qdrant+redis.
# Invocado pelo LaunchAgent ~/Library/LaunchAgents/com.axon.backends.plist no login.

set -u

# PATH precisa incluir docker (Docker Desktop ou Homebrew)
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

COMPOSE_FILE="/Users/samdev/dev/axon/docker-compose.yml"
LOG="/Users/samdev/dev/axon/data/backends-start.log"
mkdir -p "$(dirname "$LOG")"

ts() { date '+%Y-%m-%dT%H:%M:%S%z'; }

echo "[$(ts)] axon-backends-start: waiting for docker daemon..." >>"$LOG"

# Espera até 5 min pelo daemon (Docker Desktop pode demorar pra subir no login)
for i in $(seq 1 60); do
  if docker info >/dev/null 2>&1; then
    echo "[$(ts)] docker ready after ${i} attempts" >>"$LOG"
    break
  fi
  sleep 5
done

if ! docker info >/dev/null 2>&1; then
  echo "[$(ts)] ✖ docker daemon não ficou pronto em 5min — abortando" >>"$LOG"
  exit 1
fi

echo "[$(ts)] starting qdrant + redis via $COMPOSE_FILE" >>"$LOG"
docker compose -f "$COMPOSE_FILE" up -d qdrant redis >>"$LOG" 2>&1
rc=$?
echo "[$(ts)] compose up exited rc=$rc" >>"$LOG"
exit $rc
