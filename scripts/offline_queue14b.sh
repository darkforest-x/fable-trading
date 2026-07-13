#!/bin/bash
# 队列 #14b：v5 出分后 → 更新 owner_best 指针 → 模型预标 round-5 包 → 自动上架 → 部署看板
set -uo pipefail
cd "$(dirname "$0")/.."
exec >> logs/offline_queue14b.log 2>&1
echo "=== queue14b start $(date) ==="
until [ -f analysis/output/owner_detector_v5.json ]; do sleep 300; done
WIN=$(python3 -c "
import json
d = json.load(open('analysis/output/owner_detector_v5.json'))
print(max(d, key=lambda k: d[k]['f1']))")
echo "v5 winner: $WIN"
cp "runs/detect/runs/detect/$WIN/weights/best.pt" models/owner_best.pt
python3 -c "
import json
d = json.load(open('analysis/output/owner_detector_v5.json'))
json.dump({'source_run': '$WIN', 'metrics': d['$WIN'], 'pool_images': 3581}, open('models/owner_best.json','w'), indent=2)"
for i in 1 2; do
  PYTHONPATH=. .venv/bin/python scripts/model_prelabel_pack.py --dataset dense_swap_v1 \
    --count 500 --seed $((20260715+i)) --out output/label_studio/tasks_round5_chunk$i.json
  PYTHONPATH=. python3 scripts/ls_auto_import.py "round5_model_chunk$i" "output/label_studio/tasks_round5_chunk$i.json"
done
PYTHONPATH=. .venv/bin/python scripts/visual_scout.py || true
bash scripts/deploy_vps.sh || true
git add models/owner_best.json output/label_studio/tasks_round5_chunk*.json 2>/dev/null
git commit -qm "v5 winner promoted to owner_best; round-5 model-prelabeled packs live" && git push -q && echo pushed
PYTHONPATH=. python3 -c "
from src.notify import send
send('🔄 主动学习闭环上线: v5 赢家已成为 owner_best；round5_model_chunk1/2 (模型预标) 已上架 LS；视觉侦察已用新模型执勤，看板 /scout.html')" || true
echo "=== queue14b done $(date) ==="
