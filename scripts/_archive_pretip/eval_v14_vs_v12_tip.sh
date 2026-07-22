#!/usr/bin/env bash
# H-DET-1 discovery eval (v14 MAD-on pad200): tip metrics vs v12.
# Mirror of eval_v13_vs_v12_tip.sh with v14 paths. Does NOT promote.
#
# Usage:
#   bash scripts/eval_v14_vs_v12_tip.sh --dry-run
#   bash scripts/eval_v14_vs_v12_tip.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-.venv/bin/python}"
[[ -x "$PY" ]] || PY=python3

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]] || [[ "${DRY_RUN_ONLY:-0}" == "1" ]]; then
  DRY_RUN=1
fi

V12="${V12_WEIGHTS:-models/owner_best.pt}"
V14="${V14_WEIGHTS:-models/owner_v14_pad200.pt}"
TIP_LIST="${TIP_SMOKE_LIST:-analysis/output/tip_smoke_forced_windows.json}"
FORWARD_SNAP="${FORWARD_LOG_SNAP:-analysis/output/forward_log_vps_20260721.csv}"
FORWARD_LIVE="${FORWARD_LOG_LIVE:-data/forward_log.csv}"

FORWARD_LOG=""
if [[ -f "$FORWARD_SNAP" ]]; then
  FORWARD_LOG="$FORWARD_SNAP"
elif [[ -f "$FORWARD_LIVE" ]]; then
  FORWARD_LOG="$FORWARD_LIVE"
fi

echo "=== H-DET-1 v14 eval preflight $(date) ==="
echo "dry_run=$DRY_RUN"

MISSING=0
check() {
  local label="$1" path="$2"
  if [[ -f "$path" ]]; then
    echo "OK  $label: $path"
  else
    echo "MISS $label: $path"
    MISSING=1
  fi
}

check "v12_weights" "$V12"
check "v14_weights" "$V14"
check "tip_detectability" "scripts/tip_detectability.py"
check "diag_forward_detect_lag" "scripts/diag_forward_detect_lag.py"
check "v11_dataset_yaml" "datasets/dense_owner_v11/data.yaml"
if [[ -n "$FORWARD_LOG" ]]; then
  echo "OK  forward_log: $FORWARD_LOG"
else
  echo "MISS forward_log: $FORWARD_SNAP or $FORWARD_LIVE (tip-smoke will SKIP)"
fi
check "tip_smoke_list" "$TIP_LIST"

cat <<'WARN'

*** CHECKLIST — do NOT use v14 val mAP as tip success ***
  [ ] Discovery pass = tip-smoke edge fire >> v12 (0/27), NOT val mAP↑
  [ ] true_tip tip_hit is secondary (H-DET-7)
  [ ] NEVER auto-promote ACTIVE / owner_best / frozen from this script
WARN

if [[ "$DRY_RUN" == "1" ]]; then
  echo "=== dry-run done missing=$MISSING ==="
  exit 0
fi

if [[ ! -f "$V14" ]]; then
  echo "MISSING v14 weights: $V14"
  exit 1
fi
if [[ ! -f "$V12" ]]; then
  echo "MISSING v12 weights: $V12"
  exit 1
fi

echo "=== H-DET-1 v14 eval $(date) ==="
echo "v12=$V12"
echo "v14=$V14"

echo "--- 1) true_tip tip_hit (v14) ---"
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. "$PY" \
  scripts/tip_detectability.py --true-tip --split val --limit 120 \
  --dataset datasets/dense_owner_v11 \
  --weights "$V14" \
  --out analysis/output/tip_rate_v14_pad200.json

echo "--- 2) tip-smoke (v14) ---"
if [[ -n "$FORWARD_LOG" ]]; then
  OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. "$PY" \
    scripts/diag_forward_detect_lag.py --from-log --tip-smoke \
    --log "$FORWARD_LOG" \
    --weights "$V14" \
    --out analysis/output/diag_tip_smoke_v14.json \
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
v13 = load("analysis/output/tip_rate_v13_pad200.json")
v14 = load("analysis/output/tip_rate_v14_pad200.json")
s12 = load("analysis/output/diag_tip_smoke.json")
s13 = load("analysis/output/diag_tip_smoke_v13.json")
s14 = load("analysis/output/diag_tip_smoke_v14.json")

def smoke_fired(d):
    tip = (d.get("tip_smoke") or {}).get("tip") or {}
    return tip.get("n_fired"), tip.get("n_symbols")

f12, n12 = smoke_fired(s12)
f13, n13 = smoke_fired(s13)
f14, n14 = smoke_fired(s14)
print(f"true_tip tip_hit  v12={v12.get('tip_hit_rate')}  v13={v13.get('tip_hit_rate')}  v14={v14.get('tip_hit_rate')}")
print(f"tip-smoke fired   v12={f12}/{n12}  v13={f13}/{n13}  v14={f14}/{n14}")
print("Pass (discovery): tip-smoke fired(v14) >> fired(v12) AND tip_edge kept; do NOT promote.")
print("FORBIDDEN: citing v14 val mAP alone as tip success.")
print("Next: analysis/p_v14_pad200_train.md + owner decide.")
PY

echo "=== done $(date) ==="
echo "NOTE: NOT promoted. See docs/RESEARCH_AGENDA_DETECT.md H-DET-1"
