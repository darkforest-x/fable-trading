#!/bin/bash
# Local launchd fallback for owner_v15_tipval (MPS). Prefer Windows 3060.
# Does NOT promote owner_best / ACTIVE. Does NOT touch holdout / forward_log.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs analysis/output models

PY="${PY:-.venv/bin/python}"
export PYTHONPATH=.
export PYTHONUNBUFFERED=1
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export PYTORCH_ENABLE_MPS_FALLBACK=1

echo "=== owner_v15_tipval start $(date) ==="
DATA=datasets/dense_owner_v15_tipval
[ -f "$DATA/data.yaml" ] || { echo "MISSING $DATA"; exit 1; }
[ -f "$DATA/tipval_summary.json" ] || { echo "MISSING tipval_summary — finish build"; exit 1; }

BASE=models/owner_v12_htip.pt
[ -f "$BASE" ] || BASE=models/owner_best.pt
echo "--- base=$BASE"

BATCH="${BATCH:-8}"
WORKERS="${WORKERS:-2}"
DEVICE="${DEVICE:-mps}"

caffeinate -i "$PY" -m src.detection.train \
  --data "$DATA/data.yaml" \
  --model "$BASE" \
  --epochs 40 --patience 10 \
  --batch "$BATCH" --workers "$WORKERS" \
  --cache false \
  --device "$DEVICE" \
  --name owner_v15_tipval

W=runs/detect/runs/detect/owner_v15_tipval/weights/best.pt
[ -f "$W" ] || W=runs/detect/owner_v15_tipval/weights/best.pt
[ -f "$W" ] || { echo "TRAIN FAILED"; exit 1; }
cp -f "$W" models/owner_v15_tipval.pt
echo "--- stable: models/owner_v15_tipval.pt"
echo "=== done $(date) === NOTE: do NOT promote"
