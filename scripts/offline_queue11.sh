#!/bin/bash
# 离线队列 #11：合约(SWAP)检测数据集渲染 + round-4 打标弹药（等 queue10 让出机器）
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
exec >> logs/offline_queue11.log 2>&1
echo "=== queue11 start $(date) ==="
until grep -q "queue10 done" logs/offline_queue10.log 2>/dev/null; do sleep 300; done
echo "--- [1/2] render swap detection dataset"
PYTHONPATH=. python3 -m src.detection.build_dataset --out datasets/dense_swap_v1 \
  --stride 100 --max-images 5000 --symbol-contains _USDT_SWAP --seed 20260713
echo "--- [2/2] round-4 task packs (swap, with exemplar config)"
for i in 1 2 3; do
  PYTHONPATH=. python3 scripts/label_studio_prepare_import.py --dataset datasets/dense_swap_v1 \
    --split train --limit 500 --seed $((20260714+i)) --out output/label_studio/tasks_round4_swap_chunk$i.json --stratify
done
python3 - <<'PYEOF'
import json
used = set()
for i in (1, 2, 3):
    p = f'output/label_studio/tasks_round4_swap_chunk{i}.json'
    tasks = [t for t in json.load(open(p)) if t['data'].get('stem') not in used]
    for t in tasks: used.add(t['data']['stem'])
    json.dump(tasks, open(p, 'w'), ensure_ascii=False)
    print(p, len(tasks))
PYEOF
git add output/label_studio/tasks_round4_swap_chunk*.json output/label_studio/label_config_v2.xml src/detection/build_dataset.py 2>/dev/null
git commit -qm "Round-4 swap labeling ammo + exemplar-enabled label config" && git push -q && echo pushed
PYTHONPATH=. python3 -c "
from src.notify import send
send('📦 round-4 合约弹药就绪: 3 x ~500 张 SWAP 图（output/label_studio/tasks_round4_swap_chunk1-3.json），新项目请粘 label_config_v2.xml（含⭐标杆选项）')" || true
echo "=== queue11 done $(date) ==="
