#!/usr/bin/env bash
# v10 paper live loop: refresh tip klines → scan tip-only → TG+Bark on fresh hits.
# PAPER ONLY. Does not write forward_log.csv, does not place orders, does not promote.
# Target: accumulate ~100 fresh paper fires in analysis/output/live_signals_v10/paper_signals.csv
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=.
LOG_DIR=logs
mkdir -p "$LOG_DIR" analysis/output/live_signals_v10
TARGET_FRESH="${TARGET_FRESH:-100}"
INTERVAL_SEC="${INTERVAL_SEC:-900}"   # 15 min
MAX_SEND="${MAX_SEND:-8}"

count_fresh() {
  ./.venv/bin/python3 - <<'PY'
from pathlib import Path
import pandas as pd
p = Path("analysis/output/live_signals_v10/paper_signals.csv")
if not p.exists():
    print(0); raise SystemExit
df = pd.read_csv(p)
# count rows marked fresh=True (string or bool)
if "fresh" not in df.columns:
    print(0); raise SystemExit
n = int(df["fresh"].astype(str).str.lower().isin(["true", "1", "yes"]).sum())
print(n)
PY
}

echo "v10 paper loop start target_fresh=$TARGET_FRESH interval=${INTERVAL_SEC}s"
while true; do
  n=$(count_fresh || echo 0)
  echo "==== $(date -u +%Y-%m-%dT%H:%M:%SZ) paper_fresh_rows=$n / $TARGET_FRESH ===="
  if [ "$n" -ge "$TARGET_FRESH" ]; then
    echo "reached target $TARGET_FRESH fresh paper rows — stop loop"
    break
  fi
  echo "[1/2] refresh kline tips…"
  ./.venv/bin/python3 scripts/refresh_kline_tip.py --workers 8 || echo "refresh warn (continue)"
  echo "[2/2] v10 tip-only scan + send…"
  ./.venv/bin/python3 scripts/live_signal_tg.py --tip-only --send --max-send "$MAX_SEND" || echo "scan warn"
  n2=$(count_fresh || echo 0)
  echo "after pulse paper_fresh_rows=$n2"
  if [ "$n2" -ge "$TARGET_FRESH" ]; then
    echo "reached target — stop"
    break
  fi
  echo "sleep ${INTERVAL_SEC}s…"
  sleep "$INTERVAL_SEC"
done
