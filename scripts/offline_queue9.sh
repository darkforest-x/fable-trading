#!/bin/bash
# 离线队列 #9：v2 底座三对照（scratch / 官方预训练 / E2.1 迁移）在现有 268 张金标准上
# 目的：等 round-3 数据到位时，v2 直接用已证明最优的底座配方。
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
exec >> logs/offline_queue9.log 2>&1
PY=.venv/bin/python
echo "=== queue9 start $(date) ==="
for BASE in yolo11s.yaml yolo11s.pt runs/detect/runs/detect/dense_15m_full_s_e21/weights/best.pt; do
  NAME="owner_base_$(basename $BASE | tr '.' '_')"
  echo "--- training $NAME from $BASE"
  caffeinate -i $PY -m src.detection.train --data datasets/dense_owner_v1/data.yaml \
    --model "$BASE" --epochs 80 --patience 20 --name "$NAME"
done
$PY - <<'PYEOF'
import json
from pathlib import Path
from src.detection.owner_eval import evaluate_owner_f1
out = {}
for run in ('owner_base_yolo11s_yaml','owner_base_yolo11s_pt','owner_base_best_pt'):
    w = Path(f'runs/detect/runs/detect/{run}/weights/best.pt')
    if not w.exists(): continue
    best, _ = evaluate_owner_f1(w, 'datasets/dense_owner_v1')
    out[run] = best
    print(run, best, flush=True)
Path('analysis/output/owner_base_comparison.json').write_text(json.dumps(out, indent=2))
PYEOF
git add analysis/output/owner_base_comparison.json 2>/dev/null
git commit -qm "Owner-detector base comparison (scratch vs pretrained vs E2.1)" && git push -q && echo pushed
PYTHONPATH=. python3 -c "
from src.notify import send
import json
d = json.load(open('analysis/output/owner_base_comparison.json'))
send('🧪 v2底座三对照: ' + ' | '.join(f\"{k.split('_')[-2]}:{v['f1']}\" for k,v in d.items()))" || true
echo "=== queue9 done $(date) ==="
