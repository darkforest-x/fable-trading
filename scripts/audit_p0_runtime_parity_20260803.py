#!/usr/bin/env python3
"""Write the P0 runtime/research parity matrix without training or holdout reads.

Inputs are repository metadata, already-produced pre-holdout audit JSON, source
configuration, and whole-file hashes. The script never parses a candidate pool,
loads a booster, scores a row, touches models/ACTIVE, or writes data/.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / "analysis/output/p0_runtime_parity_audit_20260803.json"
BASELINE = PROJECT / "analysis/output/p0_safety_baseline_20260803/artifact_hashes.json"
SIDECAR = PROJECT / "models/frozen_tp5_sl2_swap_yolo_v10_reg_20260731.json"
RETURN_AUDIT = PROJECT / "analysis/output/p0_return_semantics_20260803.json"
RESEARCH_AUDIT = PROJECT / "analysis/output/diag_kronos_feature_value.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=PROJECT, text=True).strip()


def main() -> int:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    sidecar = json.loads(SIDECAR.read_text(encoding="utf-8"))
    returns = json.loads(RETURN_AUDIT.read_text(encoding="utf-8"))
    research = json.loads(RESEARCH_AUDIT.read_text(encoding="utf-8"))
    active_target = (PROJECT / "models/ACTIVE").read_text(encoding="utf-8").strip()
    active_path = PROJECT / active_target
    detector_path = PROJECT / "models/owner_short_star_v10.pt"
    protected = (
        "models/ACTIVE",
        "data/forward_log.csv",
        "data/executor_ledger.jsonl",
    )
    protected_now = {
        item: {
            "baseline_sha256": baseline[item]["sha256"],
            "current_sha256": sha256(PROJECT / item),
            "unchanged": baseline[item]["sha256"] == sha256(PROJECT / item),
        }
        for item in protected
    }

    active = {
        "identity": "models/ACTIVE legacy v10 runtime artifact",
        "active_pointer": active_target,
        "model_sha256": sha256(active_path),
        "sidecar_sha256": sha256(SIDECAR),
        "detector_sha256": sha256(detector_path),
        "dataset_path": sidecar["dataset_path"],
        "dataset_sha256": sidecar["dataset_sha256"],
        "source_pool": sidecar["source_pool"],
        "side": "short",
        "objective": sidecar["objective"],
        "target": sidecar["target_column"],
        "feature_count": len(sidecar["feature_columns"]),
        "feature_semantics": "legacy_unaligned",
        "best_iteration": sidecar["best_iteration"],
        "selector": {
            "quantile_name": "q90",
            "threshold": sidecar["threshold_val_q90"],
            "operator": returns["threshold_operator"],
            "tie_policy": returns["tie_policy"],
            "calibration_pass_rate": returns["calibration_pass_rate"],
            "threshold_equal_rate": returns["threshold_equal_rate"],
        },
        "target_semantics": returns["target_semantics"],
        "reporting_route": "taker",
        "execution_status": "legacy_audit_only",
        "walkforward_all_folds_net_positive": sidecar["walkforward"][
            "all_folds_net_positive"
        ],
    }
    research_reference = {
        "identity": "2026-07-30 BASE 28+19 research arm",
        "evidence": "analysis/output/diag_kronos_feature_value.json",
        "candidate_pool": research["pool"],
        "rows_after_preholdout_kronos_join": research["n"],
        "side": "short",
        "objective": "regression",
        "target": research["ret_col"],
        "feature_count": 47,
        "feature_semantics": "legacy_unaligned research features",
        "boost_rounds_per_fold": 250,
        "early_stopping_metric": None,
        "selector": {
            "mode": "per-fold exact score q90 comparison",
            "operator": ">=",
            "observed_selected_fraction": "approximately 10%; boundary tie count audited as zero",
        },
        "reporting_route": "net_taker",
        "model_sha256": None,
        "model_sha_reason": "CPCV refits were not frozen as one deployable artifact",
        "headline_lift_bp": research["arms"][0]["lift_bp"],
        "folds_positive": research["arms"][0]["folds_pos"],
        "n_folds": research["arms"][0]["n_folds"],
    }
    parity = {
        "candidate_pool": {
            "active": active["source_pool"],
            "research": research_reference["candidate_pool"],
            "match": False,
            "note": "common wide-pool origin, but active freeze has 18,379 rows and research used 18,255 joined rows",
        },
        "objective": {"active": active["objective"], "research": "regression", "match": True},
        "target": {"active": active["target"], "research": research_reference["target"], "match": True},
        "feature_count": {"active": 28, "research": 47, "match": False},
        "feature_semantics": {
            "active": active["feature_semantics"],
            "research": research_reference["feature_semantics"],
            "match": False,
        },
        "boosting": {
            "active": "best_iteration=1 from frozen model",
            "research": "250 fixed rounds per CPCV fold",
            "match": False,
        },
        "selector": {
            "active": "fixed q90 threshold with >=; pass 91.13%; equal 86.16%",
            "research": "per-fold q90; approximately 10%; zero boundary ties",
            "match": False,
        },
        "return_cost_route": {
            "active": "target net_barrier_taker; report route was historically ambiguous",
            "research": "net_barrier_taker evaluated directly",
            "match": "target_only",
        },
        "model_identity": {
            "active": active["model_sha256"],
            "research": None,
            "match": False,
            "reason": research_reference["model_sha_reason"],
        },
    }
    result = {
        "audit_version": "p0_runtime_parity_audit_20260803",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo": {
            "branch": git("branch", "--show-current"),
            "head": git("rev-parse", "HEAD"),
            "origin_main": git("rev-parse", "origin/main"),
            "active_bundle_exists": (PROJECT / "models/active_bundle.json").exists(),
        },
        "holdout": {
            "read": False,
            "boundary": "2026-05-04T00:00:00Z",
            "audited_dataset_max_signal_time": returns["dataset_max_signal_time"],
        },
        "active_runtime": active,
        "research_reference": research_reference,
        "parity": parity,
        "parity_verdict": (
            "REJECTED: the ACTIVE 28-feature one-tree fixed-threshold artifact is not "
            "the 47-feature 250-round CPCV research arm; its lift may not be transferred."
        ),
        "hypotheses": {
            "H1": {"baseline": "confirmed", "p0": "repaired_fail_closed"},
            "H2": {"baseline": "confirmed", "p0": "isolated_not_promoted"},
            "H3": {"baseline": "confirmed", "p0": "audited_and_execution_ineligible"},
            "H4": {"baseline": "confirmed", "p0": "canonical_route_repaired"},
            "H5": {"baseline": "confirmed", "p0": "exact_bundle_required; none activated"},
            "H6": {"baseline": "confirmed", "p0": "causal_fill_timeline_repaired"},
            "H7": {"baseline": "confirmed", "p0": "global_age_gate_repaired"},
        },
        "protected_artifacts": protected_now,
        "safety": {
            "trained": False,
            "holdout_read": False,
            "active_changed": not protected_now["models/ACTIVE"]["unchanged"],
            "forward_log_changed": not protected_now["data/forward_log.csv"]["unchanged"],
            "ledger_changed": not protected_now["data/executor_ledger.jsonl"]["unchanged"],
            "deployed": False,
            "order_triggered": False,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
