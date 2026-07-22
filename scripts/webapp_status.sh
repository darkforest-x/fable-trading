#!/usr/bin/env bash
# Status for local dashboard (launchd com.fable.local-webapp / :8642).
# Usage: bash scripts/webapp_status.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL=com.fable.local-webapp
UID_NUM="$(id -u)"
DOMAIN="gui/${UID_NUM}"

echo "=== local webapp status $(date '+%Y-%m-%d %H:%M:%S') ==="

if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
  state="$(launchctl print "$DOMAIN/$LABEL" 2>/dev/null | awk -F'= ' '/state =/{print $2; exit}')"
  pid="$(launchctl print "$DOMAIN/$LABEL" 2>/dev/null | awk -F'= ' '/pid =/{print $2; exit}')"
  echo "launchd: loaded  state=${state:-?}  pid=${pid:-—}"
else
  echo "launchd: NOT loaded  (start: bash scripts/webapp_start.sh)"
fi

if lsof -nP -iTCP:8642 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "listen: YES on 127.0.0.1:8642"
  lsof -nP -iTCP:8642 -sTCP:LISTEN | awk 'NR==1 || /LISTEN/'
else
  echo "listen: NO"
fi

code="$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 2 http://127.0.0.1:8642/ 2>/dev/null || echo fail)"
echo "curl /: $code"
echo "URL: http://127.0.0.1:8642/#explore"
if [[ -f "$ROOT/logs/local_webapp.err.log" ]]; then
  echo "--- err log (tail) ---"
  tail -5 "$ROOT/logs/local_webapp.err.log" || true
fi
