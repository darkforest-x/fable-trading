#!/usr/bin/env bash
# H-DET-1 discovery eval: v13 pad200 vs v12 tip metrics.
# Does NOT promote. Does NOT touch holdout. Safe to run after train finishes.
#
# Prefer: models/owner_v13_pad200.pt (stable copy from pipeline).
# Mid-run best.pt under runs/.../owner_v13_pad200/weights/ is noisy — wait for train exit.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-.venv/bin/python}"
[[ -x "$PY" ]] || PY=python3

V12="${V12_WEIGHTS:-models/owner_best.pt}"
V13="${V13_WEIGHTS:-models/owner_v13_pad200.pt}"
if [[ ! -f "$V13" ]]; then
  CAND=runs/detect/runs/detect/owner_v13_pad200/weights/best.pt
  [[ -f "$CAND" ]] && V13="$CAND"
fi
if [[ ! -f "$V13" ]]; then
  echo "MISSING v13 weights. Wait for train/pipeline (models/owner_v13_pad200.pt)."
  exit 1
fi
if [[ ! -f "$V12" ]]; then
  echo "MISSING v12 weights: $V12"
  exit 1
fi

echo "=== H-DET-1 eval $(date) ==="
echo "v12=$V12"
echo "v13=$V13"

echo "--- 1) true_tip tip_hit (v13)"
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. "$PY" \
  scripts/tip_detectability.py --true-tip --split val --limit 120 \
  --dataset datasets/dense_owner_v11 \
  --weights "$V13" \
  --out analysis/output/tip_rate_v13_pad200.json

echo "--- 2) tip-smoke (v13) — needs kline for log symbols; may no-op locally"
if [[ -f analysis/output/forward_log_vps_20260721.csv ]] || [[ -f data/forward_log.csv ]]; then
  OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. "$PY" \
    scripts/diag_forward_detect_lag.py --from-log --tip-smoke \
    --weights "$V13" \
    --out analysis/output/diag_tip_smoke_v13.json \
    || echo "WARN: tip-smoke failed (often missing kline on laptop) — run on VPS read-only"
else
  echo "SKIP tip-smoke: no forward_log snapshot"
fi

echo "--- 3) summary"
"$PY" - <<'PY'
import json
from pathlib import Path

def load(p):
    path = Path(p)
    return json.loads(path.read_text()) if path.exists() else {}

v12 = load("analysis/output/tip_rate_v12.json")
v13 = load("analysis/output/tip_rate_v13_pad200.json")
s12 = load("analysis/output/diag_tip_smoke.json")
s13 = load("analysis/output/diag_tip_smoke_v13.json")

def smoke_fired(d):
    tip = (d.get("tip_smoke") or {}).get("tip") or {}
    return tip.get("n_fired"), tip.get("n_symbols")

f12, n12 = smoke_fired(s12)
f13, n13 = smoke_fired(s13)
print(f"true_tip tip_hit  v12={v12.get('tip_hit_rate')}  v13={v13.get('tip_hit_rate')}")
print(f"tip-smoke fired   v12={f12}/{n12}  v13={f13}/{n13}")
print("Pass (discovery): tip-smoke fired(v13) >> fired(v12) AND tip_edge kept; do NOT promote.")
print("Next: analysis/p_v13_pad200_train.md + owner preview if smoke>0.")
PY

echo "=== done $(date) ==="
echo "NOTE: NOT promoted. See docs/RESEARCH_AGENDA_DETECT.md H-DET-1"
