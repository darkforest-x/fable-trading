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
from ultralytics import YOLO
import sys
sys.path.insert(0, 'scripts')
from golden_disagreement import iou
model = YOLO('runs/detect/runs/detect/dense_owner_v1/weights/best.pt')
val_img = Path('datasets/dense_owner_v1/images/val')
val_lbl = Path('datasets/dense_owner_v1/labels/val')
def load_txt(p):
    if not p.exists(): return []
    return [tuple(map(float, l.split()[1:])) for l in p.read_text().splitlines() if len(l.split())==5]
best = None
for conf in (0.20, 0.30, 0.40, 0.50):
    tp=fp=fn=0
    for img in sorted(val_img.glob('*.png')):
        gt = load_txt(val_lbl / (img.stem + '.txt'))
        res = model.predict(str(img), conf=conf, verbose=False)[0]
        preds = [tuple(map(float, b)) for b in res.boxes.xywhn.cpu().numpy()] if res.boxes is not None else []
        used=set()
        for g in gt:
            m = next((k for k,p in enumerate(preds) if k not in used and iou(g,p)>=0.3), None)
            if m is None: fn+=1
            else: used.add(m); tp+=1
        fp += len(preds)-len(used)
    prec = tp/max(tp+fp,1); rec = tp/max(tp+fn,1); f1 = 2*prec*rec/max(prec+rec,1e-9)
    row = {'conf': conf, 'f1': round(f1,3), 'p': round(prec,3), 'r': round(rec,3), 'tp': tp, 'fp': fp, 'fn': fn}
    print(row)
    if best is None or f1 > best['f1']: best = row
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
