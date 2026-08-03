"""Independent machine audit of frozen P2 outputs; never trains or reads holdout.

The audit recomputes the decision from saved success gates, verifies the model,
dataset, preregistration, selector, matched-control permutation, corrected
foldwise aggregation, and protected runtime hashes.  It accepts integrity of a
REJECTED strategy result; it cannot change that strategy verdict.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import lightgbm as lgb
import pandas as pd

from scripts.run_p2_l2_20260803 import (
    _weighted_exact_top_aggregate,
    _weighted_rank_aggregate,
)
from src.judgment.p1_dataset import file_sha256
from src.judgment.p2_l2 import exact_block_signflip_pvalue

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / "analysis/output"
RESULTS = OUT / "p2_l2_results_20260803.json"
PREREG = OUT / "p2_l2_prereg_20260803.json"
SELECTOR = OUT / "p2_l2_selector_manifest_20260803.json"
BINDING = OUT / "p2_l2_dataset_binding_20260803.json"
PAIRS = OUT / "p2_l2_matched_pairs_20260803.csv"
AUDIT = OUT / "p2_l2_independent_audit_20260803.json"
EXPECTED_DATASET_SHA = "aade2a334448d6443e71fb0d3dbbfcf450390875ce60e1f800f6dbe9c855e93a"
EXPECTED_PREREG_SHA = "38e4c474323bc03f269168db6a030575ce94ffbd4e69652403d54539da7a72b6"
PROTECTED = {
    "models/ACTIVE": "899c36259950a3d376067958ec040638253defa9ef545fa51af2a004f95bb6ef",
    "data/forward_log.csv": "6035eb60482481fb60d7e73aa72dd15d1b8884ee4c2da5410fbffa18b17b34bb",
    "data/executor_ledger.jsonl": "de85b3dded80717a1bc0399411c6fc59c2f11842095aac2e105b0d128941fe39",
}


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} is not an object")
    return value


def _same(left: Any, right: Any) -> bool:
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(
        right, sort_keys=True, allow_nan=False
    )


def build_audit() -> dict[str, Any]:
    results = _json(RESULTS)
    prereg = _json(PREREG)
    selector = _json(SELECTOR)
    binding = _json(BINDING)
    model_path = PROJECT / results["main"]["model_path"]
    model = lgb.Booster(model_file=str(model_path))
    pairs = pd.read_csv(PAIRS)
    permutation = exact_block_signflip_pvalue(pairs)
    folds = results["walkforward"]["folds"]
    expected_rank = _weighted_rank_aggregate(folds, baseline=False)
    expected_exact = _weighted_exact_top_aggregate(folds)
    decision_checks = results["success_gates"]
    expected_verdict = "accepted" if all(decision_checks.values()) else "rejected"

    checks = {
        "results_verdict_recomputes": results["verdict"] == expected_verdict == "rejected",
        "preregistration_accepted_and_exact": prereg["status"] == "accepted"
        and file_sha256(PREREG) == EXPECTED_PREREG_SHA,
        "dataset_binding_exact": binding["dataset_sha256"] == EXPECTED_DATASET_SHA
        and binding["data_sources_used"]
        == ["data/p1/p1_short_l2_preholdout_aade2a334448d644.csv"],
        "selector_is_research_only": selector["research_only"] is True
        and selector["execution_eligible"] is False
        and selector["promotion_eligible"] is False,
        "selector_verdict_matches": selector["p2_verdict"] == results["verdict"],
        "model_hash_matches": file_sha256(model_path) == results["main"]["model_sha256"]
        == selector["model_sha256"],
        "model_is_one_tree_as_reported": model.num_trees() == 1
        and results["main"]["model_health"]["best_iteration"] == 1,
        "fold_count_is_five": len(folds) == 5,
        "foldwise_rank_aggregation_matches": _same(
            expected_rank, results["walkforward"]["aggregate"]["rank"]
        ),
        "foldwise_exact_top_aggregation_matches": _same(
            expected_exact, results["walkforward"]["aggregate"]["exact_top_decile"]
        ),
        "aggregation_correction_did_not_retrain": results["aggregation_correction"][
            "training_rerun"
        ]
        is False,
        "matched_pairs_hash_matches": file_sha256(PAIRS)
        == results["walkforward"]["matched_control"]["pairs_sha256"],
        "matched_lift_recomputes": abs(
            permutation["observed_lift"]
            - results["walkforward"]["matched_control"]["lift"]
        )
        < 1e-15,
        "matched_permutation_recomputes": permutation["p_value"]
        == results["walkforward"]["matched_control"]["permutation"]["p_value"],
        "protected_hashes_unchanged": all(
            file_sha256(PROJECT / path) == digest for path, digest in PROTECTED.items()
        ),
        "active_bundle_absent": not (PROJECT / "models/active_bundle.json").exists(),
        "safety_flags_all_false": all(
            results["safety"][key] is False
            for key in (
                "holdout_read",
                "active_modified",
                "active_bundle_created",
                "deployed",
                "trading_client_accessed",
                "ordered",
            )
        ),
    }
    return {
        "audit_version": "p2_l2_independent_artifact_audit_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "artifact_integrity_verdict": "accepted" if all(checks.values()) else "rejected",
        "strategy_verdict": results["verdict"],
        "checks": checks,
        "recomputed": {
            "expected_strategy_verdict": expected_verdict,
            "main_model_trees": model.num_trees(),
            "walkforward_positive_fixed_gate_folds": results["walkforward"][
                "positive_fixed_gate_folds"
            ],
            "aggregate_fixed_gate_pressure_net": results["walkforward"]["aggregate"][
                "fixed_gate"
            ]["mean_pressure_net"],
            "matched_lift": permutation["observed_lift"],
            "matched_permutation_p": permutation["p_value"],
        },
        "artifact_hashes": {
            "results": file_sha256(RESULTS),
            "preregistration": file_sha256(PREREG),
            "selector_manifest": file_sha256(SELECTOR),
            "dataset_binding": file_sha256(BINDING),
            "model": file_sha256(model_path),
            "matched_pairs": file_sha256(PAIRS),
        },
        "safety": {
            "holdout_read": False,
            "trained": False,
            "active_modified": False,
            "deployed": False,
            "ordered": False,
        },
    }


def main() -> int:
    audit = build_audit()
    AUDIT.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0 if audit["artifact_integrity_verdict"] == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
