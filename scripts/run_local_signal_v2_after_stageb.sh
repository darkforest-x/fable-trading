#!/usr/bin/env bash
# After Stage-B dataset build finishes: audit P0 gates → if green, kick P1 cold train on 3060.
# Owner auth 2026-08-07: auto-enter P1 when hard gates pass. Never promote ACTIVE.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH=.:../yoyo-trading
DS=datasets/local_signal_v2_stageb
LOG=logs/local_signal_v2_after_stageb.log
mkdir -p logs analysis/output reports

{
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) waiting for build PID if any ==="
  # Wait until build process gone and summary exists
  while pgrep -f "build_local_signal_v2_stageb.py" >/dev/null 2>&1; do
    sleep 30
  done
  if [[ ! -f "$DS/stageb_summary.json" ]]; then
    echo "ERROR: no stageb_summary.json after build"
    exit 2
  fi

  echo "=== audit ==="
  .venv/bin/python scripts/audit_local_signal_v2.py \
    --dataset "$DS" \
    --out analysis/output/p0_local_signal_v2_stageb_audit.json

  PASS=$(.venv/bin/python -c "import json; print(json.load(open('analysis/output/p0_local_signal_v2_stageb_audit.json'))['p0_pass'])")
  echo "p0_pass=$PASS"

  .venv/bin/python - <<'PY'
import json
from datetime import datetime, timezone
from pathlib import Path
audit = json.loads(Path("analysis/output/p0_local_signal_v2_stageb_audit.json").read_text())
decision = {
    "phase": "P0",
    "candidate": "local_signal_v2_stageb",
    "decision": "accepted" if audit["p0_pass"] else "rejected",
    "baseline": "A_legacy_200k_owner_v10_chain + stage_a_w20_midbox",
    "gates": {
        "causal_leakage": "pass" if audit["gates"]["causal_dataset (visible_end <= decision)"] else "fail",
        "split_integrity": "pass" if audit["gates"]["time_based_split"] and audit["gates"]["no_event_crosses_split"] else "fail",
        "holdout_clean": "pass" if audit["gates"]["no_holdout_in_training"] else "fail",
        "manifest_conserved": "pass" if audit["gates"]["manifest_conserved"] else "fail",
        "box_end_le_decision": "pass" if audit["gates"]["box_end <= decision"] else "fail",
        "labels_in_bounds": "pass" if audit["gates"]["labels_in_bounds"] else "fail",
        "event_precision_vs_baseline": "not_applicable_until_p1",
        "fp_per_1000_vs_baseline": "not_applicable_until_p1",
        "recall_floor": "not_applicable_until_p1",
    },
    "p0_pass": audit["p0_pass"],
    "audit_path": "analysis/output/p0_local_signal_v2_stageb_audit.json",
    "notes": [
        "Stage A w20 midbox remains exploratory only; production gate is Stage B",
        "No ACTIVE / owner_best promote",
    ],
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
Path("reports/ACCEPTANCE_DECISION.json").write_text(json.dumps(decision, indent=2, ensure_ascii=False))
print(json.dumps(decision, indent=2, ensure_ascii=False))
PY

  if [[ "$PASS" != "True" ]]; then
    echo "P0 failed — stop before training (spec §14)"
    exit 3
  fi

  echo "=== P0 green → P1 cold train on 3060 (if reachable) ==="
  if [[ -x scripts/train_local_signal_v2_stageb_on_3060.sh ]]; then
    bash scripts/train_local_signal_v2_stageb_on_3060.sh || echo "train kickoff returned $?"
  else
    echo "train script missing — skip kickoff"
  fi
} >>"$LOG" 2>&1
