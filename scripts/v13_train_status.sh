#!/usr/bin/env bash
# CPU-only status for owner_v13_pad200 train. Safe while MPS is busy.
# Usage: bash scripts/v13_train_status.sh
set -euo pipefail
cd "$(dirname "$0")/.."
echo "=== v13 pad200 status $(date) ==="
if pgrep -f 'src.detection.train.*owner_v13_pad200' >/dev/null 2>&1; then
  echo "train: ALIVE"
  ps -p "$(pgrep -f 'src.detection.train.*owner_v13_pad200' | head -1)" -o pid,etime,%cpu,rss,command
else
  echo "train: DEAD / finished"
fi
STABLE=models/owner_v13_pad200.pt
MID=runs/detect/runs/detect/owner_v13_pad200/weights/best.pt
echo "stable_pt: $( [[ -f $STABLE ]] && echo YES || echo no) $STABLE"
echo "midrun_pt: $( [[ -f $MID ]] && ls -lh "$MID" | awk '{print $5,$6,$7,$8,$9}')"
if [[ -f logs/owner_v13_pad200_train.log ]]; then
  echo "--- recent epoch headers (no progress spam) ---"
  # Prefer clean "Starting training" / bare Epoch table headers; drop carriage-return spam.
  tr '\r' '\n' < logs/owner_v13_pad200_train.log \
    | grep -E 'Starting training|^\s+[0-9]+/40\s+[0-9]' \
    | sed 's/\x1b\[[0-9;]*m//g' \
    | tail -12 || true
fi
echo "--- next (after stable weights) ---"
echo "bash scripts/eval_v13_vs_v12_tip.sh"
echo "(do NOT FORCE_MIDRUN while train alive; do NOT promote)"
