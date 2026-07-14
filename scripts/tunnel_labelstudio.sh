#!/usr/bin/env bash
# Reverse-tunnel local Label Studio (:8081) to a VPS public port.
#
# On your Mac (while Label Studio docker is running):
#   export VPS_HOST=user@your.vps.ip   # default root@103.214.174.58
#   export VPS_PORT=18081              # port opened on VPS
#   bash scripts/tunnel_labelstudio.sh
#
# Also publishes :8080 -> :8081 (legacy dual-port mode used by some VPS nginx).
# Set ONLY_PRIMARY=1 to tunnel only VPS_PORT.
#
# Keep this terminal open. Ctrl-C stops. Auto-reconnects on drop.
# Security: prefer VPS firewall / nginx basic auth; LS local password is weak.
set -uo pipefail

VPS_HOST="${VPS_HOST:-root@103.214.174.58}"
VPS_PORT="${VPS_PORT:-18081}"
LOCAL_PORT="${LOCAL_PORT:-8081}"
ONLY_PRIMARY="${ONLY_PRIMARY:-0}"

if [[ -z "$VPS_HOST" ]]; then
  echo "Set VPS_HOST, e.g. export VPS_HOST=root@103.x.x.x" >&2
  exit 1
fi

echo "Tunnel: VPS :${VPS_PORT} (and :8080 unless ONLY_PRIMARY=1) --> 127.0.0.1:${LOCAL_PORT}"
echo "Keep this terminal open. Ctrl-C stops the tunnel."
echo "On VPS you may need (once): GatewayPorts clientspecified + ufw allow ${VPS_PORT}/tcp"

while true; do
  if [[ "$ONLY_PRIMARY" == "1" ]]; then
    ssh -N -R "0.0.0.0:${VPS_PORT}:127.0.0.1:${LOCAL_PORT}" \
      -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
      -o ExitOnForwardFailure=yes \
      "$VPS_HOST"
  else
    ssh -N \
      -R "0.0.0.0:8080:127.0.0.1:${LOCAL_PORT}" \
      -R "0.0.0.0:${VPS_PORT}:127.0.0.1:${LOCAL_PORT}" \
      -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
      -o ExitOnForwardFailure=yes \
      "$VPS_HOST"
  fi
  echo "ssh dropped; reconnect in 10s…" >&2
  sleep 10
done
