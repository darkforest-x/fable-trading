#!/usr/bin/env bash
# CPU-only status for owner_v14_pad200 train. Safe while MPS is busy.
# Usage: bash scripts/v14_train_status.sh
set -euo pipefail
cd "$(dirname "$0")/.."
LABEL=com.fable.owner-v14-pad200-train
UID_NUM="$(id -u)"
DOMAIN="gui/${UID_NUM}"

echo "=== v14 pad200 status $(date) ==="

if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
  state="$(launchctl print "$DOMAIN/$LABEL" 2>/dev/null | awk -F'= ' '/state =/{print $2; exit}')"
  pid="$(launchctl print "$DOMAIN/$LABEL" 2>/dev/null | awk -F'= ' '/pid =/{print $2; exit}')"
  echo "launchd: loaded  state=${state:-?}  pid=${pid:-—}"
else
  echo "launchd: NOT loaded  (start: bash scripts/v14_train_start.sh)"
fi

if pgrep -f 'src.detection.train.*owner_v14_pad200' >/dev/null 2>&1 \
  || pgrep -f 'train_owner_v14_pad200\.sh' >/dev/null 2>&1; then
  echo "train: ALIVE"
  pgrep -f 'src.detection.train.*owner_v14_pad200|train_owner_v14_pad200\.sh' \
    | head -3 \
    | while read -r p; do
        ps -p "$p" -o pid,etime,%cpu,rss,command 2>/dev/null || true
      done
else
  echo "train: DEAD / finished / not started"
fi

STABLE=models/owner_v14_pad200.pt
MID=runs/detect/runs/detect/owner_v14_pad200/weights/best.pt
echo "stable_pt: $( [[ -f $STABLE ]] && echo YES || echo no) $STABLE"
if [[ -f $MID ]]; then
  echo "midrun_pt: $(ls -lh "$MID" | awk '{print $5,$6,$7,$8,$9}')"
else
  echo "midrun_pt: (none yet)"
fi

LOG=logs/owner_v14_pad200_train.log
if [[ -f $LOG ]]; then
  bytes=$(wc -c <"$LOG" | tr -d ' ')
  mtime=$(stat -f '%Sm' -t '%Y-%m-%d %H:%M:%S' "$LOG" 2>/dev/null || true)
  echo "log: $LOG  bytes=$bytes  mtime=$mtime"
  echo "--- recent epoch headers (no progress spam) ---"
  tr '\r' '\n' <"$LOG" \
    | grep -E 'owner_v14_pad200 start|Starting training|finetune=|^\s+[0-9]+/40\s+[0-9]|stable:|done |TRAIN FAILED|Error|OOM|killed' \
    | sed 's/\x1b\[[0-9;]*m//g' \
    | tail -16 || true
else
  echo "log: (missing) $LOG"
fi

echo "--- notes ---"
echo "do NOT promote owner_best / ACTIVE; Windows sync deferred"
echo "stop: launchctl bootout $DOMAIN/$LABEL"
