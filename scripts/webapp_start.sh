#!/usr/bin/env bash
# Install + load local dashboard under launchd (survives Cursor agent shell death).
# Does not use MPS; safe alongside v13 train.
# Usage: bash scripts/webapp_start.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL=com.fable.local-webapp
PLIST_SRC="$ROOT/scripts/${LABEL}.plist"
PLIST_DST="$HOME/Library/LaunchAgents/${LABEL}.plist"
UID_NUM="$(id -u)"
DOMAIN="gui/${UID_NUM}"

mkdir -p "$ROOT/logs" "$HOME/Library/LaunchAgents"

if [[ ! -x "$ROOT/.venv/bin/uvicorn" ]]; then
  echo "ERROR: missing $ROOT/.venv/bin/uvicorn" >&2
  exit 1
fi
if [[ ! -f "$PLIST_SRC" ]]; then
  echo "ERROR: missing $PLIST_SRC" >&2
  exit 1
fi

# Drop any leftover agent-shell uvicorn on 8642 (do not touch v13 train).
if lsof -nP -iTCP:8642 -sTCP:LISTEN >/dev/null 2>&1; then
  # Only kill listeners that look like our uvicorn, not unrelated services.
  PIDS="$(lsof -nP -iTCP:8642 -sTCP:LISTEN -t 2>/dev/null || true)"
  for p in $PIDS; do
    cmd="$(ps -p "$p" -o command= 2>/dev/null || true)"
    if echo "$cmd" | grep -qE 'uvicorn.*src\.webapp\.server|uvicorn.*8642'; then
      echo "stopping stale listener pid=$p"
      kill "$p" 2>/dev/null || true
    fi
  done
  sleep 1
fi

cp "$PLIST_SRC" "$PLIST_DST"

# Idempotent reload
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST_DST"
launchctl enable "$DOMAIN/$LABEL" 2>/dev/null || true
# Kick in case RunAtLoad already fired before enable
launchctl kickstart -k "$DOMAIN/$LABEL" 2>/dev/null || true

# uvicorn import can take a couple seconds under load (v13 train)
sleep 2
bash "$ROOT/scripts/webapp_status.sh"
echo "URL: http://127.0.0.1:8642/#explore"
echo "stop: launchctl bootout $DOMAIN/$LABEL"
