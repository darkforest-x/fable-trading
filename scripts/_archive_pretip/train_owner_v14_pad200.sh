#!/bin/bash
# pad200 v14 (MAD-on): finetune from owner_v12_htip. launchd-friendly — no exec
# redirect (stdout/stderr go to plist Standard*Path).
# Does NOT promote owner_best / ACTIVE. Does NOT touch VPS / forward_log / holdout.
# Stable copy only after train: models/owner_v14_pad200.pt
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

echo "=== owner_v14_pad200 start $(date) ==="

DATA=datasets/dense_owner_v14_pad200
if [ ! -f "$DATA/data.yaml" ]; then
  echo "MISSING dataset $DATA — see analysis/p_v14_pad200_rebuild.md"
  exit 1
fi

BASE=models/owner_v12_htip.pt
if [ ! -f "$BASE" ]; then
  BASE=models/owner_best.pt
fi
echo "--- base weights: $BASE"

# Match v13 launchd stable: batch=8 workers=2 cache=false (avoid 16GB OOM)
BATCH="${BATCH:-8}"
WORKERS="${WORKERS:-2}"
DEVICE="${DEVICE:-mps}"
echo "--- train batch=$BATCH workers=$WORKERS device=$DEVICE cache=false"

caffeinate -i "$PY" -m src.detection.train \
  --data "$DATA/data.yaml" \
  --model "$BASE" \
  --epochs 40 --patience 10 \
  --batch "$BATCH" --workers "$WORKERS" \
  --cache false \
  --device "$DEVICE" \
  --name owner_v14_pad200

W=runs/detect/runs/detect/owner_v14_pad200/weights/best.pt
if [ ! -f "$W" ]; then
  W=runs/detect/owner_v14_pad200/weights/best.pt
fi
echo "--- weights=$W"
if [ ! -f "$W" ]; then
  echo "TRAIN FAILED: no best.pt"
  exit 1
fi

# Stable copy — NOT promoted to owner_best / ACTIVE
cp -f "$W" models/owner_v14_pad200.pt
echo "--- stable: models/owner_v14_pad200.pt"
echo "=== owner_v14_pad200 done $(date) ==="
echo "NOTE: do NOT promote. Status: bash scripts/v14_train_status.sh"
