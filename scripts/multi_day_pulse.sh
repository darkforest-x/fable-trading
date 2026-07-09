#!/bin/bash
# Lightweight multi-day heartbeat. Never kills train. Safe to cron/screen loop.
set -u
cd "$(dirname "$0")/.." || exit 1
OUT=output/offline_tasks/MULTI_DAY_STATUS.md
mkdir -p output/offline_tasks logs
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
TRAIN_ALIVE=0
pgrep -f 'src.detection.train' >/dev/null 2>&1 && TRAIN_ALIVE=1
RES=runs/detect/runs/detect/dense_15m_full_s_e21/results.csv
NEPOCH=0
BEST="n/a"
PATIENCE="n/a"
if [ -f "$RES" ]; then
  METRICS=$(python3 - <<'PY'
import csv
from pathlib import Path
p = Path("runs/detect/runs/detect/dense_15m_full_s_e21/results.csv")
rows = list(csv.DictReader(p.open())) if p.exists() else []
print(len(rows))
if rows:
    b = max(rows, key=lambda r: float(r["metrics/mAP50(B)"]))
    last = rows[-1]
    be = int(float(b["epoch"]))
    le = int(float(last["epoch"]))
    print(f"ep{be} mAP50={float(b['metrics/mAP50(B)']):.4f}")
    print(max(0, 12 - (le - be)))
else:
    print("none")
    print("n/a")
PY
)
  NEPOCH=$(printf '%s\n' "$METRICS" | sed -n '1p')
  BEST=$(printf '%s\n' "$METRICS" | sed -n '2p')
  PATIENCE=$(printf '%s\n' "$METRICS" | sed -n '3p')
fi
PARTS=$(find data/kline_fetched -maxdepth 1 -name '*.part.csv' 2>/dev/null | wc -l | tr -d ' ')
FO=$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 2 http://127.0.0.1:5151/ || echo 000)
LS=$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 2 http://127.0.0.1:8081/ || echo 000)
FW=$(wc -l < data/forward_log.csv 2>/dev/null | tr -d ' ')
H1=$(wc -l < data/forward_log_h1_scaled.csv 2>/dev/null | tr -d ' ')
DF=$(df -h /System/Volumes/Data 2>/dev/null | tail -1 | awk '{print $4" free ("$5")"}')
FORMAL=0
[ -f analysis/p2a_e21_train_report.md ] && FORMAL=1
cat > "$OUT" <<MD
# Multi-day status $TS

Owner away. Agent continues per \`AUTONOMOUS_CHARTER.md\` — **do not stop**.

## fable 拍板
SWAP · EMA 8-55 · 冻结 TP5/SL2 · YOLO 非关键 · H1 影子 · VPS \`ENABLE_JOB_EXECUTOR=0\`

## Pulse
| check | value |
|-------|-------|
| YOLO train alive | $TRAIN_ALIVE |
| E2.1 epochs logged | $NEPOCH |
| E2.1 best | $BEST |
| patience_left_est | $PATIENCE |
| formal report | $FORMAL |
| .part leftovers | $PARTS |
| FO :5151 | $FO |
| LS :8081 | $LS |
| forward_log lines | $FW |
| H1 shadow lines | $H1 |
| disk | $DF |

## Waiting
- Train exit → finalize formal report + consistency + FO hard_e21
- No holdout / no ACTIVE swap / no auto BLOCKED expand
MD
echo "pulse wrote $OUT train=$TRAIN_ALIVE best=$BEST patience=$PATIENCE parts=$PARTS"
