#!/usr/bin/env bash
# H-DET-1 discovery eval: v13 pad200 vs v12 tip metrics.
# Does NOT promote. Does NOT touch holdout. Safe to run after train finishes.
#
# Prefer: models/owner_v13_pad200.pt (stable copy from pipeline).
# Mid-run best.pt under runs/.../owner_v13_pad200/weights/ is noisy — wait for train exit.
#
# Usage:
#   bash scripts/v13_train_status.sh              # CPU status while train runs
#   bash scripts/eval_v13_vs_v12_tip.sh --dry-run # CPU preflight only (safe while v13 trains)
#   bash scripts/eval_v13_vs_v12_tip.sh           # full (needs stable models/owner_v13_pad200.pt)
#
# Morning path after train exits:
#   1) ls -lh models/owner_v13_pad200.pt   # or copy from runs/.../weights/best.pt
#   2) bash scripts/eval_v13_vs_v12_tip.sh
#   3) Owner decides promote — script NEVER auto-promotes
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-.venv/bin/python}"
[[ -x "$PY" ]] || PY=python3

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]] || [[ "${DRY_RUN_ONLY:-0}" == "1" ]]; then
  DRY_RUN=1
fi

V12="${V12_WEIGHTS:-models/owner_best.pt}"
V13="${V13_WEIGHTS:-models/owner_v13_pad200.pt}"
TIP_LIST="${TIP_SMOKE_LIST:-analysis/output/tip_smoke_forced_windows.json}"
FORWARD_SNAP="${FORWARD_LOG_SNAP:-analysis/output/forward_log_vps_20260721.csv}"
FORWARD_LIVE="${FORWARD_LOG_LIVE:-data/forward_log.csv}"

# --- resolve forward log (snapshot preferred on laptop) ---
FORWARD_LOG=""
if [[ -f "$FORWARD_SNAP" ]]; then
  FORWARD_LOG="$FORWARD_SNAP"
elif [[ -f "$FORWARD_LIVE" ]]; then
  FORWARD_LOG="$FORWARD_LIVE"
fi

echo "=== H-DET-1 eval preflight $(date) ==="
echo "dry_run=$DRY_RUN"

# --- refuse mid-run weights while train process alive (unless forced) ---
TRAIN_ALIVE=0
if pgrep -f 'src.detection.train.*owner_v13_pad200' >/dev/null 2>&1; then
  TRAIN_ALIVE=1
fi
echo "v13_train_alive=$TRAIN_ALIVE"

if [[ ! -f "$V13" ]]; then
  CAND=runs/detect/runs/detect/owner_v13_pad200/weights/best.pt
  if [[ -f "$CAND" ]]; then
    if [[ "$TRAIN_ALIVE" == "1" && "${FORCE_MIDRUN:-0}" != "1" ]]; then
      echo "REFUSE mid-run best.pt while train alive (set FORCE_MIDRUN=1 to override — not recommended)."
      echo "Wait for models/owner_v13_pad200.pt from pipeline."
      if [[ "$DRY_RUN" == "0" ]]; then
        exit 1
      fi
    else
      V13="$CAND"
      echo "WARN: using mid-run checkpoint $V13"
    fi
  fi
fi

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
if [[ -f "$V13" ]]; then
  echo "OK  v13_weights: $V13"
else
  echo "MISS v13_weights: models/owner_v13_pad200.pt (or mid-run best.pt)"
  MISSING=1
fi
check "tip_detectability" "scripts/tip_detectability.py"
check "diag_forward_detect_lag" "scripts/diag_forward_detect_lag.py"
check "v11_dataset_yaml" "datasets/dense_owner_v11/data.yaml"
if [[ -n "$FORWARD_LOG" ]]; then
  echo "OK  forward_log: $FORWARD_LOG"
else
  echo "MISS forward_log: $FORWARD_SNAP or $FORWARD_LIVE (tip-smoke will SKIP)"
fi
check "tip_smoke_list" "$TIP_LIST"

# val-not-pad warning (always print — H-DET-3 / EXT-3)
cat <<'WARN'

*** CHECKLIST — do NOT use v13 val mAP as tip success ***
  [ ] v13_pad200 val labels == v11 val (mid-window gold), NOT pad200 tip geometry
  [ ] Discovery pass line = tip-smoke edge fire rate >> v12 (0/27), NOT val mAP↑
  [ ] true_tip tip_hit is secondary; can be high while live tip_fire≈0 (H-DET-7)
  [ ] mid-run best.pt is NOT final H-DET-1 verdict
  [ ] NEVER auto-promote ACTIVE / frozen from this script
WARN

if [[ "$DRY_RUN" == "1" ]]; then
  echo "=== dry-run done (no YOLO / no MPS) missing=$MISSING ==="
  # missing tip list is fixable without weights; exit 0 if only waiting on v13 weights
  exit 0
fi

if [[ ! -f "$V13" ]]; then
  echo "MISSING v13 weights. Wait for train/pipeline (models/owner_v13_pad200.pt)."
  exit 1
fi
if [[ ! -f "$V12" ]]; then
  echo "MISSING v12 weights: $V12"
  exit 1
fi
if [[ "$TRAIN_ALIVE" == "1" && "${FORCE_MIDRUN:-0}" != "1" ]]; then
  echo "REFUSE: train still running. Wait for exit + stable copy."
  exit 1
fi

echo "=== H-DET-1 eval $(date) ==="
echo "v12=$V12"
echo "v13=$V13"

echo "--- 1) true_tip tip_hit (v13) — NOT val mAP; tip geometry re-render ---"
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. "$PY" \
  scripts/tip_detectability.py --true-tip --split val --limit 120 \
  --dataset datasets/dense_owner_v11 \
  --weights "$V13" \
  --out analysis/output/tip_rate_v13_pad200.json

echo "--- 2) tip-smoke (v13) — needs kline for log symbols; may no-op locally ---"
if [[ -n "$FORWARD_LOG" ]]; then
  OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. "$PY" \
    scripts/diag_forward_detect_lag.py --from-log --tip-smoke \
    --log "$FORWARD_LOG" \
    --weights "$V13" \
    --out analysis/output/diag_tip_smoke_v13.json \
    || echo "WARN: tip-smoke failed (often missing kline on laptop) — run on VPS read-only"
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
print("FORBIDDEN: citing v13 val mAP alone as tip success (val labels are unpadded mid gold).")
print("Next: analysis/p_v13_pad200_train.md + owner preview if smoke>0.")
PY

echo "=== done $(date) ==="
echo "NOTE: NOT promoted. See docs/RESEARCH_AGENDA_DETECT.md H-DET-1"
