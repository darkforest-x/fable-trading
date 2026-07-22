#!/bin/bash
# Auto-resume pad200 build until summary exists. Survives jetsam/OOM.
set -uo pipefail
cd "$(dirname "$0")/.."
OUT=datasets/dense_owner_v14_pad200
LOG=logs/build_v14_pad200.log
PY="${PY:-.venv/bin/python}"
export PYTHONPATH=. PYTHONUNBUFFERED=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
echo "=== watchdog start $(date) ===" | tee -a "$LOG"
ROUND=0
while true; do
  if [ -f "$OUT/pad200_summary.json" ] && [ -f "$OUT/data.yaml" ]; then
    echo "=== BUILD COMPLETE $(date) ===" | tee -a "$LOG"
    cat "$OUT/pad200_summary.json" | tee -a "$LOG"
    exit 0
  fi
  ROUND=$((ROUND+1))
  OK=$(find "$OUT/images/train" -name '*_pad200.png' 2>/dev/null | wc -l | tr -d ' ')
  SK=$(wc -l < "$OUT/pad200_skip.log" 2>/dev/null | tr -d ' ' || echo 0)
  echo "=== resume round=$ROUND ok=$OK skip=$SK $(date) ===" | tee -a "$LOG"
  ARGS=(--src datasets/dense_owner_v11 --out "$OUT")
  if [ -d "$OUT" ]; then ARGS+=(--resume); fi
  caffeinate -i "$PY" -u scripts/build_crop_pad200_dataset.py "${ARGS[@]}" >>"$LOG" 2>&1
  RC=$?
  echo "=== round=$ROUND exit=$RC $(date) ===" | tee -a "$LOG"
  if [ -f "$OUT/pad200_summary.json" ]; then
    echo "=== BUILD COMPLETE $(date) ===" | tee -a "$LOG"
    exit 0
  fi
  # If no progress in this round, stop (logic bug)
  OK2=$(find "$OUT/images/train" -name '*_pad200.png' 2>/dev/null | wc -l | tr -d ' ')
  SK2=$(wc -l < "$OUT/pad200_skip.log" 2>/dev/null | tr -d ' ' || echo 0)
  if [ "$OK2" -eq "$OK" ] && [ "$SK2" -eq "$SK" ]; then
    echo "=== STALLED no progress exit=$RC ===" | tee -a "$LOG"
    exit 1
  fi
  sleep 2
done
