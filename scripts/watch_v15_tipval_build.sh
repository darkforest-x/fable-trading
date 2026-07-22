#!/bin/bash
# Jetsam-safe watchdog: batch --max-new 80, respawn until tipval_summary.json.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=datasets/dense_owner_v15_tipval
LOG=logs/build_v15_tipval.log
PY="${PY:-/Users/zhangzc/fable-trading/.venv/bin/python}"
mkdir -p logs "$OUT"
export PYTHONPATH=.
export PYTHONUNBUFFERED=1
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1
export NUMEXPR_NUM_THREADS=1

echo "=== watch_v15_tipval $(date) py=$PY ===" | tee -a "$LOG"

while true; do
  if [ -f "$OUT/tipval_summary.json" ] && [ -f "$OUT/data.yaml" ]; then
    echo "DONE $(date)" | tee -a "$LOG"
    cat "$OUT/tipval_summary.json" | tee -a "$LOG"
    exit 0
  fi
  ok=$(ls "$OUT/images/val/"*_pad200.png 2>/dev/null | wc -l | tr -d ' ')
  skip=$(wc -l < "$OUT/tipval_skip.log" 2>/dev/null | tr -d ' ' || echo 0)
  echo "--- spawn $(date) pad200=$ok skip=$skip ---" | tee -a "$LOG"
  set +e
  caffeinate -i "$PY" -u scripts/build_v15_tipval_dataset.py \
    --resume --no-link-train --max-new 80 >>"$LOG" 2>&1
  RC=$?
  set -e
  echo "--- exit=$RC $(date) ---" | tee -a "$LOG"
  if [ -f "$OUT/tipval_summary.json" ]; then
    echo "DONE after exit $(date)" | tee -a "$LOG"
    cat "$OUT/tipval_summary.json" | tee -a "$LOG"
    exit 0
  fi
  sleep 2
done
