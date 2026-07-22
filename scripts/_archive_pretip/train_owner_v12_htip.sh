#!/bin/bash
# H-TIP v12: tip-augmented finetune from owner_best (v11).
# Single variable: tip-truncated positive clones in train only.
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs analysis/output
LOG=logs/owner_v12_htip_train.log
exec >>"$LOG" 2>&1
PY="${PY:-.venv/bin/python}"
export PYTHONPATH=.
echo "=== owner_v12_htip start $(date) ==="

echo "--- 1) build htip dataset"
$PY scripts/build_htip_dataset.py \
  --src datasets/dense_owner_v11 \
  --out datasets/dense_owner_v12_htip

echo "--- 2) tip baseline v11"
$PY scripts/tip_detectability.py --true-tip --split val --limit 120 \
  --dataset datasets/dense_owner_v11 --weights models/owner_best.pt \
  --out analysis/output/tip_rate_v11_baseline.json

echo "--- 3) train chain finetune"
caffeinate -i $PY -m src.detection.train \
  --data datasets/dense_owner_v12_htip/data.yaml \
  --model models/owner_best.pt \
  --epochs 40 --patience 10 \
  --name owner_v12_htip \
  --workers 4 --cache disk

W=runs/detect/runs/detect/owner_v12_htip/weights/best.pt
if [ ! -f "$W" ]; then
  W=runs/detect/owner_v12_htip/weights/best.pt
fi
echo "--- 4) weights=$W"

echo "--- 5) tip metric v12"
$PY scripts/tip_detectability.py --true-tip --split val --limit 120 \
  --dataset datasets/dense_owner_v11 --weights "$W" \
  --out analysis/output/tip_rate_v12.json

echo "--- 6) frozen owner F1"
$PY - <<PYEOF
import json
from pathlib import Path
from src.detection.owner_eval import evaluate_owner_f1
w = Path("$W")
if not w.exists():
    print("missing weights", w); raise SystemExit(1)
best, _ = evaluate_owner_f1(w, "datasets/owner_eval_frozen")
Path("analysis/output/owner_v12_htip_frozen.json").write_text(json.dumps(best, indent=2)+"\n")
print("frozen F1", best.get("f1"), "P", best.get("p"), "R", best.get("r"))
try:
    from src.notify import send
    send(f"🧠 <b>owner_v12_htip done</b>\\nF1 {best.get('f1'):.3f} P {best.get('p'):.3f} R {best.get('r'):.3f}\\nweights {w}")
except Exception as e:
    print("notify skip", e)
PYEOF

echo "=== owner_v12_htip done $(date) ==="
echo "NOTE: do not promote without owner OK. Compare tip_rate_v11 vs tip_rate_v12."
