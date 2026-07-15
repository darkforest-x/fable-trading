#!/bin/bash
# 队列 #15：owner 检测器 v6（4501张池, 冻结验证集已排除）双底座对照 + 冻结集晋升
set -uo pipefail
cd "$(dirname "$0")/.."
exec >> logs/offline_queue15.log 2>&1
PY=.venv/bin/python
echo "=== queue15 start $(date) ==="
# Speed recipe (accuracy-preserving): workers/cache/plots via train.py defaults.
# Chain fine-tune peaks early → patience 10 / epochs 40. Cold start keeps longer wait.
echo "--- training owner_v6_chain (fine-tune, patience=10)"
caffeinate -i $PY -m src.detection.train --data datasets/dense_owner_v6/data.yaml \
  --model runs/detect/runs/detect/owner_v5_from_v4/weights/best.pt \
  --epochs 40 --patience 10 --name owner_v6_chain
echo "--- training owner_v6_coco (cold start, patience=20)"
caffeinate -i $PY -m src.detection.train --data datasets/dense_owner_v6/data.yaml \
  --model yolo11s.pt \
  --epochs 100 --patience 20 --name owner_v6_coco
echo "--- 冻结集评估 + 晋升最强"
PYTHONPATH=. $PY - <<'PYEOF'
import json
from pathlib import Path
from src.detection.owner_eval import evaluate_owner_f1
prev = json.load(open('analysis/output/frozen_eval_comparison.json'))
for run in ('owner_v6_chain','owner_v6_coco'):
    w = Path(f'runs/detect/runs/detect/{run}/weights/best.pt')
    if w.exists():
        best,_ = evaluate_owner_f1(w, 'datasets/owner_eval_frozen')
        prev[run.replace('owner_','')] = best
        print(run, 'frozen-F1', best['f1'], 'P', best['p'], 'R', best['r'], flush=True)
json.dump(prev, open('analysis/output/frozen_eval_comparison.json','w'), indent=2)
PYEOF
PYTHONPATH=. $PY scripts/promote_owner_best.py
git add analysis/output/frozen_eval_comparison.json models/owner_best.json data/golden_pool.json data/exemplars.json output/label_studio/export_round5_chunk*_v5.json 2>/dev/null
git commit -qm "Owner detector v6: 4501-image pool (round5 labeled), frozen-eval promotion" && git push -q && echo pushed
PYTHONPATH=. $PY -c "
from src.notify import send
import json
d = json.load(open('analysis/output/frozen_eval_comparison.json'))
b = json.load(open('models/owner_best.json'))
curve = ' → '.join(f\"{k}:{v['f1']}\" for k,v in d.items() if 'v' in k)
send(f'🧠 v6训练完成(4501张)。冻结集曲线: {curve}。生效模型: {b[\"source_run\"]} F1 {b[\"frozen_eval_f1\"]}')" || true
echo "=== queue15 done $(date) ==="
# v6 训练+晋升完成后自动重启侦察兵（用新的 owner_best）
nohup bash scripts/scout_loop.sh >/dev/null 2>&1 & disown
echo "侦察兵已重启（新模型执勤）$(date)"
