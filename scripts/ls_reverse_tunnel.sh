#!/usr/bin/env bash
# Reverse-tunnel local Label Studio (:8081) to a VPS public port.
#
# On your Mac (while Label Studio docker is running):
#   export VPS_HOST=user@your.vps.ip
#   export VPS_PORT=18081          # port opened on VPS
#   bash scripts/ls_reverse_tunnel.sh
#
# Then at work open:  http://YOUR_VPS_IP:18081
# Login: fable-review@example.com / fable-review-local
#
# Security: LS password is weak; keep VPS firewall to your office IP if possible.
# Prefer: ssh -R bind only on localhost + nginx basic auth on VPS.
set -euo pipefail

VPS_HOST="${VPS_HOST:-root@103.214.174.58}"
VPS_PORT="${VPS_PORT:-18081}"
LOCAL_PORT="${LOCAL_PORT:-8081}"

if [[ -z "$VPS_HOST" ]]; then
  echo "Set VPS_HOST, e.g. export VPS_HOST=root@103.x.x.x" >&2
  exit 1
fi

echo "Tunnel: VPS :${VPS_PORT}  -->  127.0.0.1:${LOCAL_PORT}"
echo "Keep this terminal open. Ctrl-C stops the tunnel."
echo "On VPS you may need (once):"
echo "  # /etc/ssh/sshd_config → GatewayPorts clientspecified  (or yes)"
echo "  # sudo systemctl reload sshd"
echo "  # ufw allow ${VPS_PORT}/tcp"

# GatewayPorts clientspecified allows remote bind of 0.0.0.0:VPS_PORT
exec ssh -N -R "0.0.0.0:${VPS_PORT}:127.0.0.1:${LOCAL_PORT}" \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -o ExitOnForwardFailure=yes \
  "$VPS_HOST"
