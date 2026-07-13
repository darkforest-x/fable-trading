#!/bin/bash
# 队列 #14：v5 双底座（COCO 从头 vs v4 续训）@ 3581 张混合宇宙金标准
set -uo pipefail
cd "$(dirname "$0")/.."
exec >> logs/offline_queue14.log 2>&1
PY=.venv/bin/python
echo "=== queue14 start $(date) ==="
for PAIR in "yolo11s.pt owner_v5_coco" "runs/detect/runs/detect/owner_v4/weights/best.pt owner_v5_from_v4"; do
  set -- $PAIR
  echo "--- training $2 from $1"
  caffeinate -i $PY -m src.detection.train --data datasets/dense_owner_v5/data.yaml \
    --model "$1" --epochs 100 --patience 25 --name "$2"
done
PYTHONPATH=. $PY - <<'PYEOF'
import json
from pathlib import Path
from src.detection.owner_eval import evaluate_owner_f1
out = {}
for run in ('owner_v5_coco', 'owner_v5_from_v4'):
    w = Path(f'runs/detect/runs/detect/{run}/weights/best.pt')
    if w.exists():
        best, sweep = evaluate_owner_f1(w, 'datasets/dense_owner_v5')
        out[run] = best
        print(run, best, flush=True)
Path('analysis/output/owner_detector_v5.json').write_text(json.dumps(out, indent=2))
PYEOF
git add analysis/output/owner_detector_v5.json data/golden_pool.json data/exemplars.json output/label_studio/export_round4_swap_chunk*.json scripts/build_owner_dataset.py 2>/dev/null
git commit -qm "Owner detector v5: 3581-image mixed-universe pool, dual-base" && git push -q && echo pushed
PYTHONPATH=. python3 -c "
from src.notify import send
import json
d = json.load(open('analysis/output/owner_detector_v5.json'))
send('🧠 v5 (3581张/1868框): ' + ' | '.join(f\"{k.split('v5_')[-1]}: F1 {v['f1']} (P{v['p']}/R{v['r']})\" for k,v in d.items()) + ' — 曲线: 0.35→0.46→0.51→v5')" || true
echo "=== queue14 done $(date) ==="
