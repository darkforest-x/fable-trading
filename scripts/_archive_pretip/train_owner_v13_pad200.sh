#!/bin/bash
# pad200 v13: crop-after-box + left-pad-200 finetune from owner_v12_htip.
# Single variable vs v12: train positives are pad200 remaps (not H-TIP true_tip).
# Does NOT promote owner_best. Does NOT touch VPS / forward_log / holdout.
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs analysis/output models
LOG=logs/owner_v13_pad200_train.log
exec >>"$LOG" 2>&1
PY="${PY:-.venv/bin/python}"
export PYTHONPATH=.
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
echo "=== owner_v13_pad200 start $(date) ==="

DATA=datasets/dense_owner_v13_pad200
if [ ! -f "$DATA/data.yaml" ]; then
  echo "MISSING dataset $DATA — run build_crop_pad200_dataset.py first"
  exit 1
fi

BASE=models/owner_v12_htip.pt
if [ ! -f "$BASE" ]; then
  BASE=models/owner_best.pt
fi
echo "--- base weights: $BASE"

# 16GB Mac: conservative batch/workers. If OOM, re-run with --batch 2 or
#   bash scripts/train_on_3060.sh --name owner_v13_pad200 --dataset datasets/dense_owner_v13_pad200
BATCH="${BATCH:-4}"
WORKERS="${WORKERS:-2}"
echo "--- train batch=$BATCH workers=$WORKERS"

caffeinate -i $PY -m src.detection.train \
  --data "$DATA/data.yaml" \
  --model "$BASE" \
  --epochs 40 --patience 10 \
  --batch "$BATCH" --workers "$WORKERS" \
  --cache disk \
  --name owner_v13_pad200

W=runs/detect/runs/detect/owner_v13_pad200/weights/best.pt
if [ ! -f "$W" ]; then
  W=runs/detect/owner_v13_pad200/weights/best.pt
fi
echo "--- 4) weights=$W"
if [ ! -f "$W" ]; then
  echo "TRAIN FAILED: no best.pt"
  exit 1
fi

# Stable copy — NOT promoted to owner_best
cp -f "$W" models/owner_v13_pad200.pt
echo "--- stable: models/owner_v13_pad200.pt"

echo "--- 5) tip metric v13 (true_tip on v11 val protocol, same as v12 report)"
$PY scripts/tip_detectability.py --true-tip --split val --limit 120 \
  --dataset datasets/dense_owner_v11 --weights models/owner_v13_pad200.pt \
  --out analysis/output/tip_rate_v13_pad200.json

echo "--- 6) frozen owner F1"
$PY - <<'PYEOF'
import json
from pathlib import Path
from src.detection.owner_eval import evaluate_owner_f1
w = Path("models/owner_v13_pad200.pt")
best, _ = evaluate_owner_f1(w, "datasets/owner_eval_frozen")
Path("analysis/output/owner_v13_pad200_frozen.json").write_text(
    json.dumps(best, indent=2) + "\n"
)
print("frozen F1", best.get("f1"), "P", best.get("p"), "R", best.get("r"))
meta = {
    "name": "owner_v13_pad200",
    "created_at": __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    ).isoformat(),
    "source_run": "runs/detect/runs/detect/owner_v13_pad200/weights/best.pt",
    "stable_path": "models/owner_v13_pad200.pt",
    "base": "models/owner_v12_htip.pt",
    "dataset": "datasets/dense_owner_v13_pad200",
    "protocol": "crop_after_box_pad200",
    "promoted": False,
    "frozen_eval": best,
}
Path("models/owner_v13_pad200.json").write_text(json.dumps(meta, indent=2) + "\n")
try:
    from src.notify import send
    send(
        f"🧠 <b>owner_v13_pad200 done</b>\\n"
        f"F1 {best.get('f1'):.3f} P {best.get('p'):.3f} R {best.get('r'):.3f}\\n"
        f"NOT promoted — owner decide"
    )
except Exception as e:
    print("notify skip", e)
PYEOF

echo "=== owner_v13_pad200 done $(date) ==="
echo "NOTE: do NOT promote. Report: analysis/p_v13_pad200_train.md"
