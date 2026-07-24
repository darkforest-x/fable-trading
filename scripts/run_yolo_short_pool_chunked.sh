#!/usr/bin/env bash
# Memory-safe short YOLO judgment-pool builder (16GB Mac).
# Spawns a fresh Python every CHUNK_SERIES symbols; per-symbol CSV checkpoint
# via yolo_candidate_source.py --resume. Jetsam mid-run ≠ zero progress.
#
# Usage:
#   bash scripts/run_yolo_short_pool_chunked.sh
# Env overrides: CHUNK_SERIES (default 3), WEIGHTS, OUT, LOG, DEVICE,
#   SYMBOLS_FILE, MONTHS, END_BEFORE, START_TIME, PIDFILE
# 10hv_6m pilot example:
#   CHUNK_SERIES=1 OUT=data/judgment_yolo_owner_side_short_10hv_6m.csv \
#   SYMBOLS_FILE=analysis/output/yolo_short_10hv_6m_symbols.txt \
#   MONTHS=6 END_BEFORE=2026-05-04 \
#   LOG=analysis/output/yolo_owner_side_short_tip_v1b_10hv_6m_scan.log \
#   PIDFILE=analysis/output/yolo_owner_side_short_tip_v1b_10hv_6m_scan.pid \
#   bash scripts/run_yolo_short_pool_chunked.sh
set -u
cd "$(dirname "$0")/.."
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 OPENBLAS_NUM_THREADS=1
export MPLBACKEND=Agg
export PYTHONPATH=.
export PYTHONUNBUFFERED=1
# CPU is the stable default (MPS has hung multi-series scans historically).
export FABLE_YOLO_DEVICE="${DEVICE:-cpu}"

PY=/Users/zhangzc/fable-trading/.venv/bin/python
CHUNK_SERIES="${CHUNK_SERIES:-3}"
WEIGHTS="${WEIGHTS:-runs/detect/runs/detect/owner_side_short_tip_v1b/weights/best.pt}"
OUT="${OUT:-data/judgment_yolo_owner_side_short.csv}"
LOG="${LOG:-analysis/output/yolo_owner_side_short_tip_v1b_scan.log}"
PIDFILE="${PIDFILE:-analysis/output/yolo_owner_side_short_tip_v1b_scan.pid}"
SYMBOLS_FILE="${SYMBOLS_FILE:-}"
MONTHS="${MONTHS:-0}"
END_BEFORE="${END_BEFORE:-}"
START_TIME="${START_TIME:-}"
DONE_SIDE="${OUT}.done_symbols"

EXTRA_ARGS=()
if [[ -n "$SYMBOLS_FILE" ]]; then
  EXTRA_ARGS+=(--symbols-file "$SYMBOLS_FILE")
fi
if [[ -n "$START_TIME" ]]; then
  EXTRA_ARGS+=(--start-time "$START_TIME")
fi
if [[ -n "$END_BEFORE" ]]; then
  EXTRA_ARGS+=(--end-before "$END_BEFORE")
fi
if [[ "${MONTHS}" != "0" && -n "$MONTHS" ]]; then
  EXTRA_ARGS+=(--months "$MONTHS")
fi
# set -u: empty array expansion must be guarded
EXTRA_ARGS_EXPAND=()
if ((${#EXTRA_ARGS[@]} > 0)); then
  EXTRA_ARGS_EXPAND=("${EXTRA_ARGS[@]}")
fi

mkdir -p analysis/output data
rm -f data/_yolo_cand_tmp_*.png 2>/dev/null || true
echo $$ > "$PIDFILE"
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] driver start chunk=$CHUNK_SERIES device=$FABLE_YOLO_DEVICE weights=$WEIGHTS out=$OUT symbols_file=${SYMBOLS_FILE:-none} months=$MONTHS end_before=${END_BEFORE:-none} start_time=${START_TIME:-none}" | tee -a "$LOG"

# Total series: symbols-file line count, else full swap universe.
if [[ -n "$SYMBOLS_FILE" ]]; then
  n_total=$(grep -vE '^\s*(#|$)' "$SYMBOLS_FILE" | wc -l | tr -d ' ')
else
  n_total=$($PY - <<'PY'
from scripts.yolo_candidate_source import _list_swap_jobs
print(len(_list_swap_jobs()))
PY
)
fi

chunk=0
stall=0
while true; do
  n_done=0
  if [[ -f "$DONE_SIDE" ]]; then
    n_done=$(wc -l < "$DONE_SIDE" | tr -d ' ')
  fi
  if (( n_done >= n_total )); then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] complete: done=$n_done / $n_total" | tee -a "$LOG"
    break
  fi
  chunk=$((chunk + 1))
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] chunk=$chunk done=$n_done/$n_total" | tee -a "$LOG"
  $PY -u scripts/yolo_candidate_source.py \
    --side short \
    --weights "$WEIGHTS" \
    --out "$OUT" \
    --workers 1 \
    --resume \
    --chunk-series "$CHUNK_SERIES" \
    "${EXTRA_ARGS_EXPAND[@]}" \
    >> "$LOG" 2>&1
  rc=$?
  find data -maxdepth 1 -name '_yolo_cand_tmp_*.png' -delete 2>/dev/null || true
  n_after=0
  if [[ -f "$DONE_SIDE" ]]; then
    n_after=$(wc -l < "$DONE_SIDE" | tr -d ' ')
  fi
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] chunk=$chunk exit=$rc done_after=$n_after" | tee -a "$LOG"
  if (( rc == 139 || rc == 137 )); then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] WARN: killed/segfault rc=$rc — resume next" | tee -a "$LOG"
  elif (( rc != 0 )); then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] FATAL non-signal exit=$rc" | tee -a "$LOG"
    exit "$rc"
  fi
  if (( n_after <= n_done )); then
    stall=$((stall + 1))
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] STALL progress ($stall) — sleep 5 then retry" | tee -a "$LOG"
    if (( stall >= 3 )); then
      echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] FATAL: stalled 3 chunks" | tee -a "$LOG"
      exit 2
    fi
    sleep 5
  else
    stall=0
  fi
done

$PY -u scripts/yolo_candidate_source.py \
  --side short --weights "$WEIGHTS" --out "$OUT" \
  "${EXTRA_ARGS_EXPAND[@]}" \
  --finalize >> "$LOG" 2>&1
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] driver done" | tee -a "$LOG"
rm -f "$PIDFILE"
