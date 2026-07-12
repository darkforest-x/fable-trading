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
import json, sys
from pathlib import Path
from ultralytics import YOLO
sys.path.insert(0, 'scripts')
from golden_disagreement import iou
def load_txt(p):
    if not p.exists(): return []
    return [tuple(map(float, l.split()[1:])) for l in p.read_text().splitlines() if len(l.split())==5]
val_img = Path('datasets/dense_owner_v1/images/val')
val_lbl = Path('datasets/dense_owner_v1/labels/val')
out = {}
for run in ('owner_base_yolo11s_yaml','owner_base_yolo11s_pt','owner_base_best_pt'):
    w = Path(f'runs/detect/runs/detect/{run}/weights/best.pt')
    if not w.exists(): continue
    model = YOLO(str(w))
    best = None
    for conf in (0.15, 0.2, 0.3, 0.4):
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
        pr = tp/max(tp+fp,1); rc = tp/max(tp+fn,1); f1 = 2*pr*rc/max(pr+rc,1e-9)
        if best is None or f1 > best['f1']:
            best = {'conf': conf, 'f1': round(f1,3), 'p': round(pr,3), 'r': round(rc,3)}
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
