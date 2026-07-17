#!/usr/bin/env bash
# Full pipeline: YOLO v10 pool rescan → event backtest → rsync to VPS.
# Does NOT write models/ACTIVE (owner must promote explicitly).
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=.
PY="${PY:-.venv/bin/python}"
LOG=logs/yolo_rescan_v10.log
OUT=data/judgment_yolo_swap_v10.csv
TAG=p3_yolo_v10_reg
WORKERS="${WORKERS:-5}"
VPS="${VPS:-root@103.214.174.58}"
DIR="${DIR:-/opt/fable-trading}"

mkdir -p logs analysis/output data

echo "=== 1) YOLO rescan (owner_best=v10) workers=$WORKERS ==="
$PY -u scripts/yolo_candidate_source.py \
  --weights models/owner_best.pt \
  --out "$OUT" \
  --workers "$WORKERS" | tee "$LOG"

echo "=== 2) Event backtest (inline train judgment; no ACTIVE write) ==="
$PY -u -m src.backtest.run --data "$OUT" --tag "$TAG" | tee "logs/${TAG}_backtest.log"

echo "=== 3) Sync to VPS $VPS:$DIR ==="
rsync -az \
  models/owner_best.pt models/owner_best.json \
  models/ACTIVE models/ACTIVE_PREV \
  models/frozen_tp5_sl2_swap_yolo_v8_reg_20260716.txt \
  models/frozen_tp5_sl2_swap_yolo_v8_reg_20260716.json \
  "$VPS:$DIR/models/" 2>/dev/null || true

rsync -az \
  "$OUT" \
  data/judgment_yolo_swap_v8.csv \
  data/scored_signals_swap.csv \
  data/scored_signals_swap_meta.json \
  data/forward_log.csv \
  "$VPS:$DIR/data/" 2>/dev/null || true

rsync -az \
  "analysis/output/${TAG}_backtest.json" \
  "analysis/output/${TAG}_trades.csv" \
  "$VPS:$DIR/analysis/output/" 2>/dev/null || true

# Also push scout_mtf static/rank if present
rsync -az --exclude='__pycache__' src/scout_mtf/ "$VPS:$DIR/src/scout_mtf/" || true
rsync -az src/webapp/static/scout_mtf.* "$VPS:$DIR/src/webapp/static/" 2>/dev/null || true

ssh "$VPS" "systemctl restart fable-dashboard; sleep 2; systemctl is-active fable-dashboard"

echo "=== done ==="
echo "backtest: analysis/output/${TAG}_backtest.json"
echo "pool: $OUT"
echo "ACTIVE not changed (still v8 freeze). Promote v10 freeze only after owner OK."
