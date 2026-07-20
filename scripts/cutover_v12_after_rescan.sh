#!/usr/bin/env bash
# After judgment_yolo_swap_v12.csv exists: freeze artifact + accept backtest.
# Owner-authorized 2026-07-20 option C (v12 pool rebuild + freeze + accept).
# Holdout consumption #6 — do NOT --write-active / do NOT change default_config.
# Promote ACTIVE / default frozen requires a separate owner decision.
set -euo pipefail
cd "$(dirname "$0")/.."
LOG=logs/cutover_v12_after_rescan.log
mkdir -p logs
exec >>"$LOG" 2>&1
echo "=== cutover_v12 start $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

CSV=data/judgment_yolo_swap_v12.csv
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

echo "--- freeze v12 pool (NO --write-active) ---"
$PY scripts/freeze_model.py --yolo-v12-pool --date 20260720

echo "--- train val metrics (no --eval-holdout) ---"
$PY -m src.judgment.train --data "$CSV" --tag p3_yolo_v12_reg | tee logs/p3_yolo_v12_train.log | tail -80

echo "--- stage-3 accept backtest (frozen v12_pool; holdout #6) ---"
$PY -m src.backtest.run \
  --data "$CSV" \
  --tag p3_yolo_v12_reg \
  --frozen-config v12_pool

echo "--- summary ---"
$PY - <<'PY'
import json
from pathlib import Path
active = Path("models/ACTIVE").read_text().strip()
print("ACTIVE unchanged:", active)
art = Path("models/frozen_tp5_sl2_swap_yolo_v12_reg_20260720.json")
if art.exists():
    m = json.loads(art.read_text())
    print("v12_threshold_val_q90:", m.get("threshold_val_q90"))
    print("v12_dataset:", m.get("dataset_path"))
    print("v12_best_iteration:", m.get("best_iteration"))
bt = Path("analysis/output/p3_yolo_v12_reg_backtest.json")
if bt.exists():
    d = json.loads(bt.read_text())
    a = (d.get("cost_sweep_accept_window") or {}).get("0.003") or {}
    print("accept@0.3%:", json.dumps(a, ensure_ascii=False)[:600])
    print("acceptance_check:", d.get("acceptance_check_base_cost"))
print("done — waiting owner promote; ACTIVE/default still v11")
PY
echo "=== cutover_v12 done $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
