#!/usr/bin/env bash
# Deploy H-TIP v12 weights + enable tip-only shadow on the forward pulse.
# Does NOT promote owner_best, does NOT clear forward_log, does NOT eval holdout.
#
# Usage (from Mac with SSH to VPS):
#   bash scripts/enable_v12_shadow_vps.sh
# Env:
#   VPS_HOST=user@host  (default: see deploy_vps.sh / owner convention)
#   VPS_DIR=/opt/fable-trading
set -euo pipefail
cd "$(dirname "$0")/.."

VPS_HOST="${VPS_HOST:-root@103.214.174.58}"
VPS_DIR="${VPS_DIR:-/opt/fable-trading}"
WEIGHTS_LOCAL="${WEIGHTS_LOCAL:-models/owner_v12_htip.pt}"

if [ ! -f "$WEIGHTS_LOCAL" ]; then
  echo "missing $WEIGHTS_LOCAL — copy from runs/detect/runs/detect/owner_v12_htip/weights/best.pt first"
  exit 1
fi

echo "=== scp weights → $VPS_HOST:$VPS_DIR/models/owner_v12_htip.pt ==="
scp -o StrictHostKeyChecking=accept-new "$WEIGHTS_LOCAL" \
  "$VPS_HOST:$VPS_DIR/models/owner_v12_htip.pt"

echo "=== enable FABLE_V12_SHADOW=1 for fable-forward.service drop-in ==="
ssh "$VPS_HOST" bash -s <<'REMOTE'
set -euo pipefail
DIR=/opt/fable-trading
mkdir -p /etc/systemd/system/fable-forward.service.d
cat >/etc/systemd/system/fable-forward.service.d/v12-shadow.conf <<'EOF'
[Service]
Environment=FABLE_V12_SHADOW=1
Environment=FABLE_V12_WEIGHTS=models/owner_v12_htip.pt
EOF
# If the unit is a oneshot from a timer, drop-in still applies.
systemctl daemon-reload
# Best-effort restart timer/service names used historically
systemctl restart fable-forward.timer 2>/dev/null || true
systemctl restart fable-forward.service 2>/dev/null || true
echo "drop-in:"
cat /etc/systemd/system/fable-forward.service.d/v12-shadow.conf
echo "weights:"
ls -la "$DIR/models/owner_v12_htip.pt"
# one manual shadow tick (won't fail the script if data cold)
cd "$DIR"
if [ -x .venv/bin/python ]; then
  PYTHONPATH=. .venv/bin/python scripts/forward_track_v12_shadow.py 2>&1 | tail -20 || true
fi
echo "shadow lines=$(wc -l < data/forward_log_v12_shadow.csv 2>/dev/null || echo 0)"
REMOTE

echo "=== done. Mainline forward_log and owner_best untouched. ==="
echo "After 48h: write analysis/p_v12_shadow_48h.md"
