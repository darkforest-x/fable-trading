#!/bin/bash
# Lightweight multi-day heartbeat. Never kills train. Safe to cron/screen loop.
set -u
cd "$(dirname "$0")/.." || exit 1
ROOT=$(pwd)
OUT=${FABLE_PULSE_OUT:-output/offline_tasks/MULTI_DAY_STATUS.md}
mkdir -p "$(dirname "$OUT")" logs
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
TRAIN_ALIVE=0
pgrep -f 'dense_15m_full_s_e21b_hsv0' >/dev/null 2>&1 && TRAIN_ALIVE=1
RES=${FABLE_E21B_RESULTS:-}
if [ -z "$RES" ]; then
  for candidate in "$ROOT"/../fable-trading*/runs/detect/runs/detect/dense_15m_full_s_e21b_hsv0/results.csv; do
    if [ -f "$candidate" ]; then
      RES=$candidate
      break
    fi
  done
fi
NEPOCH=0
BEST="n/a"
PATIENCE="n/a"
if [ -n "$RES" ] && [ -f "$RES" ]; then
  METRICS=$(python3 - "$RES" <<'PY'
import csv
import sys
from pathlib import Path
p = Path(sys.argv[1])
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
FW=$(wc -l < data/forward_log_ma206.csv 2>/dev/null | tr -d ' ')
H1=$(wc -l < data/forward_log_h1_scaled_ma206.csv 2>/dev/null | tr -d ' ')
DF=$(df -h /System/Volumes/Data 2>/dev/null | tail -1 | awk '{print $4" free ("$5")"}')
FORMAL=0
[ -f analysis/p2a_e21b_hsv0_report.md ] && FORMAL=1
cat > "$OUT" <<MD
# Multi-day status $TS

Owner away. Agent continues per \`AUTONOMOUS_CHARTER.md\` — **do not stop**.

## fable 拍板
SWAP · SMA/EMA20/60/120 · 冻结 TP5/SL2 · YOLO 非关键 · H1 影子 · VPS \`ENABLE_JOB_EXECUTOR=0\`

## Pulse
| check | value |
|-------|-------|
| YOLO train alive | $TRAIN_ALIVE |
| E2.1b epochs logged | $NEPOCH |
| E2.1b best | $BEST |
| patience_left_est | $PATIENCE |
| formal report | $FORMAL |
| .part leftovers | $PARTS |
| FO :5151 | $FO |
| LS :8081 | $LS |
| forward_log lines | $FW |
| H1 shadow lines | $H1 |
| disk | $DF |

## Waiting
- E2.1b exit → formal report + consistency + fixed SAHI benchmark
- No holdout / no ACTIVE swap / no auto BLOCKED expand
MD
echo "pulse wrote $OUT train=$TRAIN_ALIVE best=$BEST patience=$PATIENCE parts=$PARTS"
