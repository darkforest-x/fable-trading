#!/bin/bash
# 离线队列 #8：owner 口味检测器 v1 —— 从 E2.1 权重迁移微调 + 对比规则基线
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
exec >> logs/offline_queue8.log 2>&1
PY=.venv/bin/python
echo "=== queue8 start $(date) ==="
caffeinate -i $PY -m src.detection.train --data datasets/dense_owner_v1/data.yaml \
  --model runs/detect/runs/detect/dense_15m_full_s_e21/weights/best.pt \
  --epochs 80 --patience 20 --name dense_owner_v1
$PY - <<'PYEOF'
import json
from pathlib import Path
from src.detection.owner_eval import evaluate_owner_f1
best, sweep = evaluate_owner_f1(
    'runs/detect/runs/detect/dense_owner_v1/weights/best.pt',
    'datasets/dense_owner_v1',
    confs=(0.20, 0.30, 0.40, 0.50),
)
for row in sweep:
    print(row)
Path('analysis/output/owner_detector_v1.json').write_text(json.dumps(best, indent=2))
print('BEST', best)
PYEOF
git add analysis/output/owner_detector_v1.json 2>/dev/null
git commit -qm "Owner-taste detector v1 result" && git push -q && echo pushed
PYTHONPATH=. python3 -c "
from src.notify import send
import json
b = json.load(open('analysis/output/owner_detector_v1.json'))
send(f\"🧠 owner口味检测器v1: F1 {b['f1']} (P {b['p']}/R {b['r']}) @conf{b['conf']} — 规则基线天花板 0.45\")" || true
echo "=== queue8 done $(date) ==="
