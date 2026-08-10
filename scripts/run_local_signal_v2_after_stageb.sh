#!/usr/bin/env bash
# After corrected Stage-B build finishes: audit P0 gates and stop at owner review.
# The handover spec §14 explicitly requires P0 to stop before large training.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH=.:../yoyo-trading
DS=datasets/local_signal_v2_stageb_strictneg_v2
LOG=logs/local_signal_v2_after_stageb.log
mkdir -p logs analysis/output reports

{
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) waiting for build PID if any ==="
  # Wait until build process gone and summary exists
  while pgrep -f "build_local_signal_v2_stageb_strictneg_v2.py" >/dev/null 2>&1; do
    sleep 30
  done
  if [[ ! -f "$DS/stageb_summary.json" ]]; then
    echo "ERROR: no stageb_summary.json after build"
    exit 2
  fi

  echo "=== audit ==="
  .venv/bin/python scripts/audit_local_signal_v2.py \
    --dataset "$DS" \
    --out analysis/output/p0_local_signal_v2_stageb_strictneg_v2_audit.json

  AUDIT_PASS=$(.venv/bin/python -c "import json; print(json.load(open('analysis/output/p0_local_signal_v2_stageb_strictneg_v2_audit.json'))['p0_pass'])")
  echo "audit_p0_pass=$AUDIT_PASS"

  .venv/bin/python - <<'PY'
import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
audit = json.loads(Path("analysis/output/p0_local_signal_v2_stageb_strictneg_v2_audit.json").read_text())

def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

positive_sha = sha256("datasets/local_signal_v2_stageb_strictneg_v2/w20_manifest.json")
negative_sha = sha256("datasets/local_signal_v2_stageb_strictneg_v2/w20_neg_manifest.json")
reproducible = (
    positive_sha == "6814b86cdda7ca62ab4b1df8e7fa9be9acc96f184475430965b00079c1b8b047"
    and negative_sha == "2cdcf8898f70a1e8e9d453c23cbf93180dec323ee133db39d14bb3cd0f5213ba"
)
p0_pass = bool(audit["p0_pass"] and reproducible)
decision = {
    "phase": "P0",
    "candidate": "local_signal_v2_stageb_strictneg_v2",
    "decision": "accepted" if p0_pass else "rejected",
    "baseline": "local_signal_v2_stageb_v1_invalidated",
    "p0_pass": p0_pass,
    "p1_train_complete": False,
    "gates": {
        "causal_leakage": "pass" if audit["gates"]["causal_dataset (visible_end <= decision)"] else "fail",
        "split_integrity_all_windows": "pass" if audit["gates"]["time_based_split"] and audit["gates"]["no_event_crosses_split"] else "fail",
        "holdout_clean": "pass" if audit["gates"]["no_holdout_in_training"] else "fail",
        "manifest_conserved": "pass" if audit["gates"]["manifest_conserved"] else "fail",
        "market_bar_traceability": "pass" if audit["gates"]["market_bar_traceability"] else "fail",
        "fixed_seed_manifest_reproducible": "pass" if reproducible else "fail",
        "event_precision_vs_baseline": "not_run",
        "fp_per_1000_vs_baseline": "not_run",
        "recall_floor": "not_run",
        "p2_auto_enter": "blocked_until_p1_matrix_and_event_gates",
    },
    "artifacts": {
        "dataset": "datasets/local_signal_v2_stageb_strictneg_v2",
        "audit": "analysis/output/p0_local_signal_v2_stageb_strictneg_v2_audit.json",
        "report": "analysis/p0_local_signal_v2_stageb_strictneg_v2_report.md",
        "report_html": "analysis/html/p0_local_signal_v2_stageb_strictneg_v2_report.html",
        "preview": "analysis/output/local_signal_v2_stageb_strictneg_v2_preview",
        "positive_manifest_sha256": positive_sha,
        "negative_manifest_sha256": negative_sha,
    },
    "invalidated_artifacts": [{
        "artifact": "analysis/output/lsv2_stageb/owner_lsv2_stageb_cold/weights/best.pt",
        "reason": "trained on V1 negatives that cross time blocks; training also used hsv_s/v=0.05",
    }],
    "forbid_still": [
        "reuse_old_weight_as_v2_candidate",
        "auto_enter_p1",
        "promote_ACTIVE",
        "promote_owner_best",
        "live_orders",
        "clear_forward_log",
        "read_holdout_without_owner_approval",
    ],
    "holdout_v2_consumed": 0,
    "notes": [
        "V1 corrected audit: 317 train negatives after train end and 296 val negatives before val start",
        "V2 corrected audit: all three negative boundary violation counts are zero",
        "P0 accepted only; stop for owner review before any P1 training per handover spec section 14",
    ],
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
Path("reports/ACCEPTANCE_DECISION.json").write_text(json.dumps(decision, indent=2, ensure_ascii=False))
print(json.dumps(decision, indent=2, ensure_ascii=False))
PY

  PASS=$(.venv/bin/python -c "import json; print(json.load(open('reports/ACCEPTANCE_DECISION.json'))['p0_pass'])")
  echo "final_p0_pass=$PASS"

  if [[ "$PASS" != "True" ]]; then
    echo "P0 failed — stop before training (spec §14)"
    exit 3
  fi

  echo "P0 green — stop for owner review before P1 (spec §14)"
} >>"$LOG" 2>&1
