#!/bin/bash
# Full pipeline: pad200 build → train owner_v13_pad200 (no promote).
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs analysis/output models
PIPE_LOG=logs/v13_pad200_pipeline.log
exec >>"$PIPE_LOG" 2>&1
PY="${PY:-.venv/bin/python}"
export PYTHONPATH=.
export PYTHONUNBUFFERED=1
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1

echo "=== v13 pad200 pipeline start $(date) ==="

OUT=datasets/dense_owner_v13_pad200
# Existing dir is OK: build uses --resume (will not clobber v11/v12).

echo "--- 1) build pad200 dataset"
# No --mad-gate: end_incl + close-corr only (16GB safe). Resume if dir exists.
BUILD_ARGS=(--src datasets/dense_owner_v11 --out "$OUT" --limit 0)
if [ -d "$OUT" ]; then
  BUILD_ARGS+=(--resume)
  echo "resuming existing $OUT"
fi
caffeinate -i "$PY" scripts/build_crop_pad200_dataset.py "${BUILD_ARGS[@]}"
BUILD_RC=$?
echo "build exit=$BUILD_RC $(date)"
if [ "$BUILD_RC" -ne 0 ] || [ ! -f "$OUT/data.yaml" ] || [ ! -f "$OUT/pad200_summary.json" ]; then
  echo "BUILD FAILED — skip train"
  exit 1
fi
cat "$OUT/pad200_summary.json"

echo "--- 2) train owner_v13_pad200"
# train script appends to its own log; call body inline to keep one pipeline
BASE=models/owner_v12_htip.pt
[ -f "$BASE" ] || BASE=models/owner_best.pt
BATCH="${BATCH:-4}"
WORKERS="${WORKERS:-2}"
echo "base=$BASE batch=$BATCH workers=$WORKERS"

caffeinate -i "$PY" -m src.detection.train \
  --data "$OUT/data.yaml" \
  --model "$BASE" \
  --epochs 40 --patience 10 \
  --batch "$BATCH" --workers "$WORKERS" \
  --cache disk \
  --name owner_v13_pad200
TRAIN_RC=$?
echo "train exit=$TRAIN_RC $(date)"

W=runs/detect/runs/detect/owner_v13_pad200/weights/best.pt
[ -f "$W" ] || W=runs/detect/owner_v13_pad200/weights/best.pt
if [ ! -f "$W" ]; then
  echo "TRAIN FAILED: no best.pt"
  exit 1
fi
cp -f "$W" models/owner_v13_pad200.pt
echo "stable: models/owner_v13_pad200.pt"

echo "--- 3) tip_hit + frozen F1"
"$PY" scripts/tip_detectability.py --true-tip --split val --limit 120 \
  --dataset datasets/dense_owner_v11 --weights models/owner_v13_pad200.pt \
  --out analysis/output/tip_rate_v13_pad200.json

"$PY" - <<'PYEOF'
import json
from pathlib import Path
from datetime import datetime, timezone
from src.detection.owner_eval import evaluate_owner_f1
w = Path("models/owner_v13_pad200.pt")
best, _ = evaluate_owner_f1(w, "datasets/owner_eval_frozen")
Path("analysis/output/owner_v13_pad200_frozen.json").write_text(
    json.dumps(best, indent=2) + "\n"
)
tip = {}
tp = Path("analysis/output/tip_rate_v13_pad200.json")
if tp.exists():
    tip = json.loads(tp.read_text())
meta = {
    "name": "owner_v13_pad200",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "source_run": "runs/detect/runs/detect/owner_v13_pad200/weights/best.pt",
    "stable_path": "models/owner_v13_pad200.pt",
    "base": "models/owner_v12_htip.pt",
    "dataset": "datasets/dense_owner_v13_pad200",
    "protocol": "crop_after_box_pad200",
    "promoted": False,
    "frozen_eval": best,
    "tip_rate_file": "analysis/output/tip_rate_v13_pad200.json",
    "tip_hit_rate": tip.get("tip_hit_rate", tip.get("hit_rate")),
}
Path("models/owner_v13_pad200.json").write_text(json.dumps(meta, indent=2) + "\n")
print("frozen F1", best.get("f1"), "P", best.get("p"), "R", best.get("r"))
print("tip_hit", meta.get("tip_hit_rate"))
PYEOF

echo "=== v13 pad200 pipeline done $(date) ==="
echo "NOTE: NOT promoted. Write analysis/p_v13_pad200_train.md"
