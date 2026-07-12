#!/bin/bash
# 离线队列 #7（YOLO 主攻线 E3）：边界矛盾诊断 → 假设确认才训练 → 双口径评估 → TG
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
exec >> logs/offline_queue7.log 2>&1
PY=.venv/bin/python
echo "=== queue7 start $(date) ==="

echo "--- [1/3] margin diagnosis + E3 dataset build"
PYTHONPATH=. $PY scripts/e3_margin_experiment.py || { echo "diagnosis failed"; exit 1; }

GAP=$(python3 -c "import json; print(json.load(open('analysis/output/e3_margin_diagnosis.json'))['diagnosis']['fn_gap_pp'])")
echo "boundary-vs-core FN gap: ${GAP}pp (gate: >=15)"
if python3 -c "import sys; sys.exit(0 if float('$GAP') >= 15 else 1)"; then
  echo "--- [2/3] hypothesis CONFIRMED -> training yolo11s on E3 (single variable: train filtering)"
  caffeinate -i $PY -m src.detection.train --data datasets/dense_15m_e3/data.yaml \
    --model yolo11s.pt --epochs 60 --patience 15 --name dense_15m_e3
  $PY -m src.detection.eval_visualize \
    --weights runs/detect/runs/detect/dense_15m_e3/weights/best.pt --n-vis 5 || true
  echo "E3 mAP50: $(python3 -c "import json; print(json.load(open('analysis/output/p2a_val_metrics.json'))['mAP50'])")"
else
  echo "--- [2/3] hypothesis NOT confirmed (gap ${GAP}pp < 15) -> skip training, save 12h"
fi

echo "--- [3/3] commit + notify"
git add analysis/output/e3_margin_diagnosis.json scripts/e3_margin_experiment.py 2>/dev/null
git commit -qm "E3 margin diagnosis result (gap ${GAP}pp)" && git push -q && echo pushed
PYTHONPATH=. python3 -c "
from src.notify import send
send('🎯 YOLO E3 边界矛盾诊断: FN差距 ${GAP}pp (阈值15)。详情看 analysis/output/e3_margin_diagnosis.json')" || true
echo "=== queue7 done $(date) ==="
