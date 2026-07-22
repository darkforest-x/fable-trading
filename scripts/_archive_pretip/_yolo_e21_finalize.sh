#!/bin/bash
set -u
cd /Users/zhangzc/fable-trading || exit 1
LOG=logs/yolo_e21_finalize_$(date +%Y%m%d_%H%M%S).log
exec > >(tee -a "$LOG") 2>&1
TPID=$(ps aux | awk '/src.detection.train/ && !/awk/ {print $2; exit}')
echo "waiting for train pid ${TPID:-none} $(date)"
if [ -n "${TPID:-}" ]; then
  while kill -0 "$TPID" 2>/dev/null; do sleep 90; done
fi
echo "train gone $(date); sleep for post hooks"
sleep 90
.venv/bin/python - <<'PY'
from pathlib import Path
import csv, json, subprocess, sys
from datetime import datetime, timezone
from ultralytics import YOLO

run = Path('runs/detect/runs/detect/dense_15m_full_s_e21')
w = run / 'weights' / 'best.pt'
rows = list(csv.DictReader((run/'results.csv').open())) if (run/'results.csv').exists() else []
best = None
for r in rows:
    ep = int(float(r['epoch']))
    P=R=m50=m95=None
    for k,v in r.items():
        ks=k.strip()
        if ks.endswith('mAP50(B)') and '95' not in ks: m50=float(v)
        if ks.endswith('mAP50-95(B)'): m95=float(v)
        if ks.endswith('precision(B)'): P=float(v)
        if ks.endswith('recall(B)'): R=float(v)
    if m50 is not None and (best is None or m50>best['mAP50']):
        best=dict(epoch=ep,P=P,R=R,mAP50=m50,mAP50_95=m95)

official=None
if w.exists():
    m=YOLO(str(w))
    metrics=m.val(data='datasets/dense_15m_full/data.yaml', imgsz=960, split='val', plots=False)
    official=dict(
        mAP50=round(float(metrics.box.map50),4),
        mAP50_95=round(float(metrics.box.map),4),
        precision=round(float(metrics.box.mp),4),
        recall=round(float(metrics.box.mr),4),
    )
    Path('analysis/output/p2a_e21_val_metrics.json').write_text(json.dumps(official, indent=2)+'\n')

cons={}
if w.exists():
    pred_dir=Path('datasets/dense_15m_full/preds_val_e21_best')
    subprocess.check_call([sys.executable, 'scripts/export_yolo_preds_for_audit.py',
        '--dataset','datasets/dense_15m_full','--split','val','--conf','0.30',
        '--weights', str(w), '--out', str(pred_dir)])
    subprocess.check_call([sys.executable, '-m', 'src.detection.consistency_check',
        '--dataset','datasets/dense_15m_full','--split','val',
        '--preds', str(pred_dir),
        '--out','analysis/output/consistency_e21_vs_new_best.json'])
    cons=json.loads(Path('analysis/output/consistency_e21_vs_new_best.json').read_text())

lines=['# P2a YOLO E2.1 formal retrain report','',
 f'**Date**: {datetime.now(timezone.utc).isoformat()}',
 '**Labels**: MAX_DENSE_BARS=12, X_PAD_PX=6',
 '**Model**: yolo11s, imgsz=960, batch=8, patience=12, SAFE_AUG',
 f'**Weights**: `{w}`','',
 '## Official val (best.pt)']
if official:
    lines += ['| metric | value |','|---|---:|']
    for k,v in official.items():
        lines.append(f'| {k} | {v} |')
    lines.append('')
    lines.append(f"Gate mAP50≥0.90: **{'PASS' if official['mAP50']>=0.90 else 'FAIL'}**")
lines += ['','## Best from results.csv', str(best), '',
 '## Consistency vs E2.1 GT',
 f"match_rate={cons.get('match_rate_vs_gt')} gate95={cons.get('gate_match_rate_ge_0_95')}",
 '','## Honesty',
 '- Not 1:1 comparable to pre-E2 mAP 0.8569 (different GT).',
 '- No holdout. Detection non-critical path.']
Path('analysis/p2a_e21_train_report.md').write_text('\n'.join(lines)+'\n')
print('WROTE', official, best, cons.get('match_rate_vs_gt'))
PY

# Recompute FO hard list on E2.1 best preds (optional; needs fiftyone tools venv)
FO_PY=""
for c in \
  /Users/zhangzc/fable-trading-codex/.venv_yolo_tools/bin/python \
  /Users/zhangzc/fable-trading/.venv_yolo_tools/bin/python; do
  if [ -x "$c" ]; then FO_PY=$c; break; fi
done
if [ -n "$FO_PY" ] && [ -d datasets/dense_15m_full/preds_val_e21_best ]; then
  echo "FO hardlist recompute with $FO_PY $(date)"
  "$FO_PY" scripts/fiftyone_label_audit.py \
    --split val \
    --preds datasets/dense_15m_full/preds_val_e21_best \
    --export-hard output/offline_tasks/fiftyone_hard_e21 \
    2>&1 || echo "FO hardlist failed (non-fatal)"
else
  echo "skip FO hardlist (no fiftyone venv or preds)"
fi

git add analysis/p2a_e21_train_report.md \
  analysis/output/p2a_e21_val_metrics.json \
  analysis/output/consistency_e21_vs_new_best.json \
  output/offline_tasks/fiftyone_hard_e21 2>/dev/null || true
git commit -m 'Finalize YOLO E2.1 retrain report, consistency, FO hardlist' || true
git push origin main || true
echo FINALIZE_DONE
