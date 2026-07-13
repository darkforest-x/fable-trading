#!/bin/bash
# 队列 #11b：立即低优先级渲染合约数据集 → 打包 → 自动导入 Label Studio → TG
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
exec >> logs/offline_queue11b.log 2>&1
echo "=== queue11b start $(date) ==="
nice -n 15 python3 -m src.detection.build_dataset --out datasets/dense_swap_v1 \
  --stride 100 --max-images 5000 --symbol-contains _USDT_SWAP --seed 20260713 2>&1 | tail -3
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
PYEOF
for i in 1 2 3; do
  PYTHONPATH=. python3 scripts/ls_auto_import.py "round4_swap_chunk$i" "output/label_studio/tasks_round4_swap_chunk$i.json"
done
git add output/label_studio/tasks_round4_swap_chunk*.json scripts/ls_auto_import.py 2>/dev/null
git commit -qm "Round-4 swap packs rendered and auto-imported into Label Studio" && git push -q && echo pushed
PYTHONPATH=. python3 -c "
from src.notify import send
send('📦✅ round-4 合约弹药已就绪并自动导入 Label Studio：round4_swap_chunk1-3（各~500张，含⭐标杆选项）。打开 http://103.214.174.58:8080 直接开标。')" || true
echo "=== queue11b done $(date) ==="
