#!/bin/bash
# 离线队列 #10：owner 检测器 v3 双底座对照（COCO 预训练 vs v1血统）@ 1268 张金标准
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
exec >> logs/offline_queue10.log 2>&1
PY=.venv/bin/python
echo "=== queue10 start $(date) ==="
for PAIR in "yolo11s.pt owner_v3_coco" "runs/detect/runs/detect/dense_owner_v1/weights/best.pt owner_v3_chain"; do
  set -- $PAIR
  echo "--- training $2 from $1"
  caffeinate -i $PY -m src.detection.train --data datasets/dense_owner_v3/data.yaml \
    --model "$1" --epochs 100 --patience 25 --name "$2"
done
$PY - <<'PYEOF'
import json
from pathlib import Path
from src.detection.owner_eval import evaluate_owner_f1
out = {}
for run in ('owner_v3_coco', 'owner_v3_chain'):
    w = Path(f'runs/detect/runs/detect/{run}/weights/best.pt')
    if not w.exists(): continue
    best, _ = evaluate_owner_f1(w, 'datasets/dense_owner_v3')
    out[run] = best
    print(run, best, flush=True)
Path('analysis/output/owner_detector_v3.json').write_text(json.dumps(out, indent=2))
PYEOF
git add analysis/output/owner_detector_v3.json data/golden_pool.json output/label_studio/export_round3_chunk2.json scripts/build_owner_dataset.py 2>/dev/null
git commit -qm "Owner detector v3: dual-base comparison on 1268 golden images" && git push -q && echo pushed
PYTHONPATH=. python3 -c "
from src.notify import send
import json
d = json.load(open('analysis/output/owner_detector_v3.json'))
send('🧠 v3双底座结果(194框验证): ' + ' | '.join(f\"{k.split('_')[-1]}: F1 {v['f1']} (P{v['p']}/R{v['r']})\" for k,v in d.items()) + ' — 参照: v1 0.35, v2 0.37, 规则 0.45')" || true
echo "=== queue10 done $(date) ==="
