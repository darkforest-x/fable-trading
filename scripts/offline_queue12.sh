#!/bin/bash
# 队列 #12：v4 —— 等 v3 对照出分，用赢家底座在 2268 张全量金标准上训练
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
exec >> logs/offline_queue12.log 2>&1
PY=.venv/bin/python
echo "=== queue12 start $(date) ==="
until [ -f analysis/output/owner_detector_v3.json ]; do sleep 180; done
BASE=$(python3 -c "
import json
d = json.load(open('analysis/output/owner_detector_v3.json'))
coco = d.get('owner_v3_coco', {}).get('f1', 0)
chain = d.get('owner_v3_chain', {}).get('f1', 0)
print('runs/detect/runs/detect/owner_v3_coco/weights/best.pt' if coco >= chain else 'runs/detect/runs/detect/owner_v3_chain/weights/best.pt')")
echo "v4 base = $BASE"
# v4 continues from v3 winner = fine-tune path
caffeinate -i $PY -m src.detection.train --data datasets/dense_owner_v4/data.yaml \
  --model "$BASE" --epochs 40 --patience 10 --name owner_v4
$PY - <<'PYEOF'
import json
from pathlib import Path
from src.detection.owner_eval import evaluate_owner_f1
best, sweep = evaluate_owner_f1(
    'runs/detect/runs/detect/owner_v4/weights/best.pt',
    'datasets/dense_owner_v4',
)
for row in sweep:
    print(row, flush=True)
Path('analysis/output/owner_detector_v4.json').write_text(json.dumps(best, indent=2))
PYEOF
git add analysis/output/owner_detector_v4.json data/golden_pool.json output/label_studio/export_round3_chunk3.json output/label_studio/export_round3_chunk4.json 2>/dev/null
git commit -qm "Owner detector v4 on 2268-image golden pool" && git push -q && echo pushed
PYTHONPATH=. python3 -c "
from src.notify import send
import json
b = json.load(open('analysis/output/owner_detector_v4.json'))
send(f\"🧠 v4 (2268张/1307框, 379框验证): F1 {b['f1']} (P{b['p']}/R{b['r']}) — 学习曲线: v1 0.35 → v2 0.37 → v3 见前条 → v4 此条\")" || true
echo "=== queue12 done $(date) ==="
