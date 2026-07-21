#!/bin/bash
# Watchdog: resume pad200 build until summary exists, then train+eval.
# Survives periodic python deaths on 16GB Mac. Does NOT promote.
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs analysis/output models
LOG=logs/v13_pad200_watchdog.log
exec >>"$LOG" 2>&1
PY="${PY:-.venv/bin/python}"
export PYTHONPATH=. PYTHONUNBUFFERED=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
OUT=datasets/dense_owner_v13_pad200

echo "=== watchdog start $(date) ==="

attempt=0
while [ ! -f "$OUT/pad200_summary.json" ]; do
  attempt=$((attempt + 1))
  n=$(find "$OUT/images/train" -name '*_pad200.png' 2>/dev/null | wc -l | tr -d ' ')
  echo "--- attempt $attempt pad_now=${n:-0} $(date) ---"
  ARGS=(--src datasets/dense_owner_v11 --out "$OUT" --limit 0)
  if [ -d "$OUT" ]; then ARGS+=(--resume); fi
  caffeinate -i "$PY" scripts/build_crop_pad200_dataset.py "${ARGS[@]}"
  rc=$?
  echo "build rc=$rc $(date)"
  n=$(find "$OUT/images/train" -name '*_pad200.png' 2>/dev/null | wc -l | tr -d ' ')
  echo "pad_after=$n"
  if [ -f "$OUT/pad200_summary.json" ]; then
    break
  fi
  # If no progress across attempts, back off harder
  sleep 2
  if [ "$attempt" -ge 80 ]; then
    echo "TOO MANY ATTEMPTS — abort"
    exit 1
  fi
done

echo "=== build complete ==="
cat "$OUT/pad200_summary.json"

BASE=models/owner_v12_htip.pt
[ -f "$BASE" ] || BASE=models/owner_best.pt
BATCH="${BATCH:-4}"
WORKERS="${WORKERS:-2}"
echo "--- train base=$BASE batch=$BATCH ---"
caffeinate -i "$PY" -m src.detection.train \
  --data "$OUT/data.yaml" \
  --model "$BASE" \
  --epochs 40 --patience 10 \
  --batch "$BATCH" --workers "$WORKERS" \
  --cache disk \
  --name owner_v13_pad200

W=runs/detect/runs/detect/owner_v13_pad200/weights/best.pt
[ -f "$W" ] || W=runs/detect/owner_v13_pad200/weights/best.pt
if [ ! -f "$W" ]; then echo "no best.pt"; exit 1; fi
cp -f "$W" models/owner_v13_pad200.pt

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
Path("analysis/output/owner_v13_pad200_frozen.json").write_text(json.dumps(best, indent=2)+"\n")
tip = {}
tp = Path("analysis/output/tip_rate_v13_pad200.json")
if tp.exists():
    tip = json.loads(tp.read_text())
meta = {
    "name": "owner_v13_pad200",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "stable_path": "models/owner_v13_pad200.pt",
    "promoted": False,
    "frozen_eval": best,
    "tip_hit_rate": tip.get("tip_hit_rate", tip.get("hit_rate")),
}
Path("models/owner_v13_pad200.json").write_text(json.dumps(meta, indent=2)+"\n")
print("frozen F1", best.get("f1"), "tip", meta.get("tip_hit_rate"))
PYEOF

echo "=== watchdog done $(date) NOT promoted ==="
