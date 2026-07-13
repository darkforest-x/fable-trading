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
import json, sys
from pathlib import Path
from ultralytics import YOLO
sys.path.insert(0, 'scripts')
from golden_disagreement import iou
def load_txt(p):
    return [tuple(map(float, l.split()[1:])) for l in p.read_text().splitlines() if len(l.split())==5] if p.exists() else []
vi, vl = Path('datasets/dense_owner_v3/images/val'), Path('datasets/dense_owner_v3/labels/val')
out = {}
for run in ('owner_v3_coco', 'owner_v3_chain'):
    w = Path(f'runs/detect/runs/detect/{run}/weights/best.pt')
    if not w.exists(): continue
    model = YOLO(str(w)); best = None
    for conf in (0.15, 0.2, 0.3, 0.4):
        tp=fp=fn=0
        for img in sorted(vi.glob('*.png')):
            gt = load_txt(vl / (img.stem + '.txt'))
            res = model.predict(str(img), conf=conf, verbose=False)[0]
            preds = [tuple(map(float, b)) for b in res.boxes.xywhn.cpu().numpy()] if res.boxes is not None else []
            used=set()
            for g in gt:
                m = next((k for k,p in enumerate(preds) if k not in used and iou(g,p)>=0.3), None)
                if m is None: fn+=1
                else: used.add(m); tp+=1
            fp += len(preds)-len(used)
        pr=tp/max(tp+fp,1); rc=tp/max(tp+fn,1); f1=2*pr*rc/max(pr+rc,1e-9)
        if best is None or f1 > best['f1']:
            best = {'conf':conf,'f1':round(f1,3),'p':round(pr,3),'r':round(rc,3),'tp':tp,'fp':fp,'fn':fn}
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
