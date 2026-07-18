#!/usr/bin/env bash
# After judgment_yolo_swap_v11.csv exists: freeze → ACTIVE → backtest → invalidate score cache.
# Owner-authorized 2026-07-18 full v11 judgment cutover (accept-window compare = holdout #4).
set -euo pipefail
cd "$(dirname "$0")/.."
LOG=logs/cutover_v11_after_rescan.log
exec >>"$LOG" 2>&1
echo "=== cutover_v11 start $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

CSV=data/judgment_yolo_swap_v11.csv
if [ ! -f "$CSV" ]; then
  echo "missing $CSV"; exit 1
fi
n=$(wc -l < "$CSV" | tr -d ' ')
echo "csv_lines=$n"
if [ "$n" -lt 1000 ]; then
  echo "too few candidates ($n lines including header)"; exit 1
fi

PY=.venv/bin/python
export PYTHONPATH=.
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1

echo "--- freeze + write ACTIVE ---"
$PY scripts/freeze_model.py --yolo-v11-pool --date 20260718 --write-active

echo "--- stage-3 backtest (uses frozen artifact; accept window reported) ---"
$PY -m src.backtest.run --data "$CSV" --tag p3_yolo_v11_reg --frozen-config default

echo "--- invalidate dashboard score cache (swap) ---"
rm -f data/scored_signals_swap.csv data/scored_signals_swap_meta.json

echo "--- summary ---"
$PY - <<'PY'
import json
from pathlib import Path
active = Path("models/ACTIVE").read_text().strip()
print("ACTIVE:", active)
meta = Path(active.replace(".txt", ".json"))
if meta.exists():
    m = json.loads(meta.read_text())
    print("threshold_val_q90:", m.get("threshold_val_q90"))
    print("dataset:", m.get("dataset_path"))
    print("best_iteration:", m.get("best_iteration"))
bt = Path("analysis/output/p3_yolo_v11_reg_backtest.json")
if bt.exists():
    d = json.loads(bt.read_text())
    print("backtest keys:", sorted(d.keys())[:20])
    # print accept window metrics if present
    for k in ("accept", "acceptance", "results", "by_cost", "summary"):
        if k in d:
            print(k, ":", json.dumps(d[k], ensure_ascii=False)[:800])
print("done")
PY
echo "=== cutover_v11 done $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
