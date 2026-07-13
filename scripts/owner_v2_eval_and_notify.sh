#!/usr/bin/env bash
# Wait for dense_owner_v2 training log to finish, then conf-sweep F1 vs owner val and TG notify.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
LOG=logs/owner_v2_train.log
WEIGHTS=runs/detect/runs/detect/dense_owner_v2/weights/best.pt
OUT=analysis/output/owner_detector_v2.json

echo "waiting for training to finish (log: $LOG)..."
for i in $(seq 1 720); do
  if grep -qE 'Results saved to|EarlyStopping|Training complete' "$LOG" 2>/dev/null && [[ -f "$WEIGHTS" ]]; then
    # ensure process gone or idle
    if ! pgrep -f 'name dense_owner_v2' >/dev/null 2>&1; then
      break
    fi
  fi
  sleep 10
done

if [[ ! -f "$WEIGHTS" ]]; then
  echo "no weights yet; abort"
  exit 1
fi

echo "evaluating $WEIGHTS"
.venv/bin/python - <<'PY'
from __future__ import annotations
import json
from pathlib import Path
from ultralytics import YOLO
import torch

ROOT = Path('.').resolve()
weights = ROOT / 'runs/detect/runs/detect/dense_owner_v2/weights/best.pt'
data = ROOT / 'datasets/dense_owner_v2/data.yaml'
# conf sweep against val using YOLO val metrics is mAP; for box F1 vs labels we use predict+match
from ultralytics.utils.metrics import box_iou
import numpy as np

model = YOLO(str(weights))
device = 'mps' if torch.backends.mps.is_available() else 'cpu'

# Use built-in val for each conf is heavy; instead one predict with low conf then filter
val_img_dir = ROOT / 'datasets/dense_owner_v2/images/val'
val_lbl_dir = ROOT / 'datasets/dense_owner_v2/labels/val'
images = sorted(val_img_dir.glob('*.png'))

def load_gt(stem):
    p = val_lbl_dir / f'{stem}.txt'
    if not p.exists() or not p.read_text().strip():
        return np.zeros((0, 4))
    boxes = []
    for line in p.read_text().splitlines():
        _, cx, cy, w, h = map(float, line.split()[:5])
        x1, y1 = cx - w/2, cy - h/2
        x2, y2 = cx + w/2, cy + h/2
        boxes.append([x1, y1, x2, y2])
    return np.array(boxes, dtype=np.float32)

# predict once at conf=0.1
preds = {}
for img in images:
    r = model.predict(str(img), conf=0.1, verbose=False, device=device)[0]
    if r.boxes is None or len(r.boxes) == 0:
        preds[img.stem] = np.zeros((0, 5), dtype=np.float32)
        continue
    xyxy = r.boxes.xyxyn.cpu().numpy()  # normalized
    conf = r.boxes.conf.cpu().numpy()
    preds[img.stem] = np.concatenate([xyxy, conf[:, None]], axis=1)

def f1_at(thr: float):
    tp = fp = fn = 0
    for img in images:
        gt = load_gt(img.stem)
        pr = preds[img.stem]
        pr = pr[pr[:, 4] >= thr][:, :4] if len(pr) else pr.reshape(0, 4)
        if len(gt) == 0 and len(pr) == 0:
            continue
        if len(gt) == 0:
            fp += len(pr); continue
        if len(pr) == 0:
            fn += len(gt); continue
        iou = box_iou(torch.tensor(gt), torch.tensor(pr)).numpy()
        used = set()
        for i in range(len(gt)):
            j = int(iou[i].argmax()) if iou[i].max() >= 0.5 else -1
            if j >= 0 and j not in used and iou[i, j] >= 0.5:
                tp += 1; used.add(j)
            else:
                fn += 1
        fp += len(pr) - len(used)
    p = tp / max(tp + fp, 1); r = tp / max(tp + fn, 1)
    f1 = 2 * p * r / max(p + r, 1e-9)
    return {'conf': thr, 'f1': round(f1, 3), 'p': round(p, 3), 'r': round(r, 3), 'tp': tp, 'fp': fp, 'fn': fn}

rows = [f1_at(c) for c in (0.2, 0.3, 0.4, 0.5)]
best = max(rows, key=lambda x: x['f1'])
out = ROOT / 'analysis/output/owner_detector_v2.json'
out.write_text(json.dumps({'best': best, 'sweep': rows}, indent=2))
print(json.dumps({'best': best, 'sweep': rows}, indent=2))
msg = (
    f"owner v2 训练完成\n"
    f"best conf={best['conf']} F1={best['f1']} P={best['p']} R={best['r']}\n"
    f"tp={best['tp']} fp={best['fp']} fn={best['fn']}\n"
    f"data: dense_owner_v2 (train boxes~301+)"
)
from src.notify import send
ok = send(msg)
print('tg', ok)
PY
