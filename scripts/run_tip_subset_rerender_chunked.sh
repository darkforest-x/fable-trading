#!/usr/bin/env bash
# Memory-safe tip-subset rerender driver.
# Spawns a fresh Python process every MAX_SYMBOLS symbols so a SIGSEGV / leak
# cannot accumulate. Resume is handled by tip_subset_backtest.py itself.
set -u
cd "$(dirname "$0")/.."
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 OPENBLAS_NUM_THREADS=1
export MPLBACKEND=Agg
export PYTHONPATH=.
PY=.venv/bin/python
MAX_SYMBOLS="${MAX_SYMBOLS:-5}"
ELIGIBLE=analysis/output/tip_subset_eligible.csv
RERENDER=analysis/output/tip_subset_rerender.csv
LOG=logs/tip_subset_rerender.log
PIDFILE=logs/tip_subset_rerender.pid

mkdir -p logs data
rm -rf data/_tip_subset_tmp_*
echo $$ > "$PIDFILE"
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] driver start max_symbols=$MAX_SYMBOLS" | tee -a "$LOG"

n_eligible=$(($(wc -l < "$ELIGIBLE") - 1))
chunk=0
peak_rss_mb=0

while true; do
  n_done=0
  if [[ -f "$RERENDER" ]]; then
    n_done=$(($(wc -l < "$RERENDER") - 1))
  fi
  if (( n_done >= n_eligible )); then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] complete: $n_done / $n_eligible" | tee -a "$LOG"
    break
  fi
  chunk=$((chunk + 1))
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] chunk=$chunk done=$n_done/$n_eligible" | tee -a "$LOG"
  # shellcheck disable=SC2086
  $PY -u scripts/tip_subset_backtest.py --stage rerender --max-symbols "$MAX_SYMBOLS" \
    >> "$LOG" 2>&1
  rc=$?
  # sample RSS of any leftover child (should be gone)
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] chunk=$chunk exit=$rc" | tee -a "$LOG"
  rm -rf data/_tip_subset_tmp_*
  if (( rc == 139 || rc == 137 )); then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] WARN: chunk killed/segfault rc=$rc — resume next" | tee -a "$LOG"
    # If a symbol repeatedly segfaults before checkpoint, progress stalls.
    # Detect stall: same n_done twice after a crash → mark nothing, just retry once more then skip.
  fi
  if (( rc != 0 && rc != 139 && rc != 137 )); then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] FATAL non-signal exit=$rc" | tee -a "$LOG"
    exit "$rc"
  fi
  n_after=0
  if [[ -f "$RERENDER" ]]; then
    n_after=$(($(wc -l < "$RERENDER") - 1))
  fi
  if (( n_after <= n_done && (rc == 139 || rc == 137) )); then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] STALL after crash — writing skip stub for next symbol" | tee -a "$LOG"
    $PY - <<'PY' >> "$LOG" 2>&1
from pathlib import Path
import pandas as pd
elig = pd.read_csv("analysis/output/tip_subset_eligible.csv")
rer_path = Path("analysis/output/tip_subset_rerender.csv")
rer = pd.read_csv(rer_path) if rer_path.exists() else pd.DataFrame()
done = set(zip(rer["source"], rer["symbol"], rer["signal_i"].astype(int))) if len(rer) else set()
rem = elig[~elig.apply(lambda r: (r.source, r.symbol, int(r.signal_i)) in done, axis=1)]
if rem.empty:
    raise SystemExit(0)
# skip ALL remaining signals of the first remaining symbol
src, sym = rem.iloc[0][["source", "symbol"]]
grp = rem[(rem.source == src) & (rem.symbol == sym)]
stubs = [{
    "source": src, "symbol": sym, "signal_i": int(r.signal_i),
    "rerender_ok": False, "skip_reason": "segfault_skip",
    "n_boxes": 0, "max_right_bar": -1, "max_right_norm": 0.0,
    "max_conf": 0.0, "tip_conf": 0.0,
    "tip_hit_strict": False, "tip_hit_92": False,
} for r in grp.itertuples()]
out = pd.concat([rer, pd.DataFrame(stubs)], ignore_index=True) if len(rer) else pd.DataFrame(stubs)
out.to_csv(rer_path, index=False)
print(f"  skipped {len(stubs)} signals for {src}/{sym} after stall")
PY
  fi
done

# Final summary pass (prints JSON even if already complete)
$PY -u scripts/tip_subset_backtest.py --stage rerender --max-symbols 0 >> "$LOG" 2>&1
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] driver done" | tee -a "$LOG"
