#!/usr/bin/env bash
# 10-coin SHORT pilot: one symbol per process (--chunk-series 1) so kills don't wipe progress.
# Honors analysis/output/SHORT_10_PILOT.lock semantics; never touches full-universe out.
set -u
cd "$(dirname "$0")/.."
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 OPENBLAS_NUM_THREADS=1
export MPLBACKEND=Agg PYTHONUNBUFFERED=1 FABLE_YOLO_DEVICE="${DEVICE:-cpu}" PYTHONPATH=.

PY=.venv/bin/python
WEIGHTS="${WEIGHTS:-runs/detect/runs/detect/owner_side_short_tip_v1b/weights/best.pt}"
OUT="${OUT:-data/judgment_yolo_owner_side_short_10.csv}"
LOG="${LOG:-analysis/output/yolo_owner_side_short_tip_v1b_10_scan.log}"
PIDFILE="${PIDFILE:-analysis/output/yolo_owner_side_short_tip_v1b_10_scan.pid}"
MAX_SERIES="${MAX_SERIES:-10}"

mkdir -p analysis/output data
echo $$ > "$PIDFILE"
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] 10coin driver start max=$MAX_SERIES out=$OUT" | tee -a "$LOG"
START=$(date +%s)

while true; do
  n_done=0
  if [[ -f "${OUT}.done_symbols" ]]; then
    n_done=$(wc -l < "${OUT}.done_symbols" | tr -d ' ')
  fi
  if (( n_done >= MAX_SERIES )); then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] complete done=$n_done/$MAX_SERIES" | tee -a "$LOG"
    break
  fi
  wall=$(( $(date +%s) - START ))
  if (( wall > 3600 )); then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] TIMEOUT done=$n_done wall=${wall}s" | tee -a "$LOG"
    exit 3
  fi
  # Never let full-universe driver steal CPU while 10-coin lock exists.
  if [[ -f analysis/output/SHORT_10_PILOT.lock ]]; then
    pkill -9 -f 'run_yolo_short_pool_chunked.sh' 2>/dev/null || true
    launchctl bootout "gui/$(id -u)/com.fable.yolo_short_pool_tip_v1b" 2>/dev/null || true
  fi
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] chunk done=$n_done/$MAX_SERIES wall=${wall}s" | tee -a "$LOG"
  $PY -u scripts/yolo_candidate_source.py \
    --side short \
    --weights "$WEIGHTS" \
    --out "$OUT" \
    --workers 1 \
    --max-series "$MAX_SERIES" \
    --resume \
    --chunk-series 1 \
    >> "$LOG" 2>&1
  rc=$?
  rm -f data/_yolo_cand_tmp_*.png 2>/dev/null || true
  n_after=0
  if [[ -f "${OUT}.done_symbols" ]]; then
    n_after=$(wc -l < "${OUT}.done_symbols" | tr -d ' ')
  fi
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] chunk exit=$rc done_after=$n_after" | tee -a "$LOG"
  if (( n_after <= n_done )); then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] STALL sleep 8" | tee -a "$LOG"
    sleep 8
  fi
done

$PY -u scripts/yolo_candidate_source.py \
  --side short --weights "$WEIGHTS" --out "$OUT" --finalize >> "$LOG" 2>&1
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] 10coin driver done wall=$(( $(date +%s)-START ))s" | tee -a "$LOG"
rm -f "$PIDFILE"
