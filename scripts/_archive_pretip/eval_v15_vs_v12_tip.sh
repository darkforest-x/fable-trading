#!/usr/bin/env bash
# v15 tip-val experiment eval: true_tip + tip-smoke vs v12/v14.
# Does NOT promote.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-.venv/bin/python}"
[[ -x "$PY" ]] || PY=python3

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

V12="${V12_WEIGHTS:-models/owner_best.pt}"
V14="${V14_WEIGHTS:-models/owner_v14_pad200.pt}"
V15="${V15_WEIGHTS:-models/owner_v15_tipval.pt}"
FORWARD_SNAP="${FORWARD_LOG_SNAP:-analysis/output/forward_log_vps_20260721.csv}"
FORWARD_LIVE="${FORWARD_LOG_LIVE:-data/forward_log.csv}"
FORWARD_LOG=""
[[ -f "$FORWARD_SNAP" ]] && FORWARD_LOG="$FORWARD_SNAP"
[[ -z "$FORWARD_LOG" && -f "$FORWARD_LIVE" ]] && FORWARD_LOG="$FORWARD_LIVE"

echo "=== v15 tipval eval preflight $(date) dry=$DRY_RUN ==="
for p in "$V12" "$V14" "$V15" scripts/tip_detectability.py scripts/diag_forward_detect_lag.py \
  datasets/dense_owner_v11/data.yaml; do
  [[ -f "$p" ]] && echo "OK  $p" || echo "MISS $p"
done
[[ -n "$FORWARD_LOG" ]] && echo "OK  forward_log=$FORWARD_LOG" || echo "MISS forward_log"

[[ "$DRY_RUN" == "1" ]] && exit 0
[[ -f "$V15" ]] || { echo "MISSING $V15"; exit 1; }

echo "--- 1) true_tip tip_hit (v15) ---"
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. "$PY" \
  scripts/tip_detectability.py --true-tip --split val --limit 120 \
  --dataset datasets/dense_owner_v11 \
  --weights "$V15" \
  --out analysis/output/tip_rate_v15_tipval.json

echo "--- 2) tip-smoke (v15) ---"
if [[ -n "$FORWARD_LOG" ]]; then
  OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. "$PY" \
    scripts/diag_forward_detect_lag.py --from-log --tip-smoke \
    --log "$FORWARD_LOG" \
    --weights "$V15" \
    --out analysis/output/diag_tip_smoke_v15.json \
    || echo "WARN: tip-smoke failed (often missing kline on laptop)"
else
  echo "SKIP tip-smoke: no forward_log snapshot"
fi

echo "--- 3) summary ---"
"$PY" - <<'PY'
import json
from pathlib import Path

def load(p):
    path = Path(p)
    return json.loads(path.read_text()) if path.exists() else {}

v12 = load("analysis/output/tip_rate_v12.json")
v14 = load("analysis/output/tip_rate_v14_pad200.json")
v15 = load("analysis/output/tip_rate_v15_tipval.json")
s12 = load("analysis/output/diag_tip_smoke.json")
s14 = load("analysis/output/diag_tip_smoke_v14.json")
s15 = load("analysis/output/diag_tip_smoke_v15.json")

def smoke_fired(d):
    tip = (d.get("tip_smoke") or {}).get("tip") or {}
    return tip.get("n_fired"), tip.get("n_symbols")

f12, n12 = smoke_fired(s12)
f14, n14 = smoke_fired(s14)
f15, n15 = smoke_fired(s15)
print(f"true_tip tip_hit  v12={v12.get('tip_hit_rate')}  v14={v14.get('tip_hit_rate')}  v15={v15.get('tip_hit_rate')}")
print(f"tip-smoke fired   v12={f12}/{n12}  v14={f14}/{n14}  v15={f15}/{n15}")
print("Pass (discovery): tip-smoke fired(v15) >> fired(v12); do NOT promote.")
print("Hypothesis B (val-only): if tip_hit recovers toward v12 while smoke still ~0 → supports '只怪 val early-stop' for forgetting, NOT for live tip.")
PY

echo "=== done $(date) === NOTE: NOT promoted"
