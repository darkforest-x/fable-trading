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
caffeinate -i $PY -m src.detection.train --data datasets/dense_owner_v4/data.yaml \
  --model "$BASE" --epochs 100 --patience 25 --name owner_v4
$PY - <<'PYEOF'
import json, sys
from pathlib import Path
from ultralytics import YOLO
sys.path.insert(0, 'scripts')
from golden_disagreement import iou
def load_txt(p):
    return [tuple(map(float, l.split()[1:])) for l in p.read_text().splitlines() if len(l.split())==5] if p.exists() else []
vi, vl = Path('datasets/dense_owner_v4/images/val'), Path('datasets/dense_owner_v4/labels/val')
model = YOLO('runs/detect/runs/detect/owner_v4/weights/best.pt'); best=None
for conf in (0.15,0.2,0.3,0.4):
    tp=fp=fn=0
    for img in sorted(vi.glob('*.png')):
        gt = load_txt(vl/(img.stem+'.txt'))
        res = model.predict(str(img), conf=conf, verbose=False)[0]
        preds = [tuple(map(float,b)) for b in res.boxes.xywhn.cpu().numpy()] if res.boxes is not None else []
        used=set()
        for g in gt:
            m = next((k for k,p in enumerate(preds) if k not in used and iou(g,p)>=0.3), None)
            if m is None: fn+=1
            else: used.add(m); tp+=1
        fp += len(preds)-len(used)
    pr=tp/max(tp+fp,1); rc=tp/max(tp+fn,1); f1=2*pr*rc/max(pr+rc,1e-9)
    row={'conf':conf,'f1':round(f1,3),'p':round(pr,3),'r':round(rc,3)}
    print(row, flush=True)
    if best is None or f1>best['f1']: best=row
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
