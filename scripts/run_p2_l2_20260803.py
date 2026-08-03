"""Execute the frozen P2-L2 protocol: fixture, dry-run, then full validation.

The command has no holdout flag and accepts no arbitrary dataset path.  It
loads one exact P1 manifest and refuses to train unless the separately committed
P2 preregistration is accepted with the Owner-approved 15 bp pressure cost and
q90 ``>=`` selector.  Outputs are research artifacts under ``analysis/output``;
the script contains no ACTIVE, deployment, service, or trading-client write.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/run_p2_l2_20260803.py fixture
  PYTHONPATH=. .venv/bin/python scripts/run_p2_l2_20260803.py dry-run
  PYTHONPATH=. .venv/bin/python scripts/run_p2_l2_20260803.py full
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.judgment.features import FEATURE_COLUMNS
from src.judgment.p1_dataset import file_sha256, load_immutable_dataset
from src.judgment.p2_l2 import (
    LABEL_COLUMN,
    TARGET_COLUMN,
    economic_metrics,
    evaluate_fixed_gate,
    exact_block_signflip_pvalue,
    exact_top_fraction_weights,
    fit_single_feature_baseline,
    matched_candidate_pairs,
    predict,
    predict_single_feature,
    rank_metrics,
    train_regressor,
    walkforward_folds,
)
from src.judgment.p2_protocol import (
    ACTUAL_COST_PRESSURE_TOTAL,
    ADDITIONAL_SLIPPAGE_ROUND_TRIP,
    HOLDOUT_CUTOFF,
    P2ProtocolError,
    apply_runtime_gate,
    calibrate_runtime_gate,
    prepare_three_way_split,
)

PROJECT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT / "analysis/output"
P1_MANIFEST = PROJECT / "data/p1/p1_short_l2_preholdout_aade2a334448d644.manifest.json"
PREREG = OUTPUT / "p2_l2_prereg_20260803.json"
FIXTURE = OUTPUT / "p2_l2_fixture_20260803.json"
DRY_RUN = OUTPUT / "p2_l2_dry_run_20260803.json"
RESULTS = OUTPUT / "p2_l2_results_20260803.json"
MODEL = OUTPUT / "p2_l2_model_20260803.txt"
SELECTOR = OUTPUT / "p2_l2_selector_manifest_20260803.json"
BINDING = OUTPUT / "p2_l2_dataset_binding_20260803.json"
IMPORTANCE = OUTPUT / "p2_l2_feature_importance_20260803.csv"
MATCHED_PAIRS = OUTPUT / "p2_l2_matched_pairs_20260803.csv"
EXPECTED_DATASET_SHA = "aade2a334448d6443e71fb0d3dbbfcf450390875ce60e1f800f6dbe9c855e93a"
EXPECTED_P1_MANIFEST_SHA = "53b8a07612dae667a184da38bf8e0a694aaae15a5fd240d5b13238da3e13d682"
EXPECTED_PREREG_SHA = "38e4c474323bc03f269168db6a030575ce94ffbd4e69652403d54539da7a72b6"
PROTECTED = (
    PROJECT / "models/ACTIVE",
    PROJECT / "data/forward_log.csv",
    PROJECT / "data/executor_ledger.jsonl",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _relative(path: Path) -> str:
    return path.relative_to(PROJECT).as_posix()


def _json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise P2ProtocolError(f"{path} is not a JSON object")
    return raw


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _protected_hashes() -> dict[str, str | None]:
    values = {_relative(path): file_sha256(path) if path.exists() else None for path in PROTECTED}
    active_bundle = PROJECT / "models/active_bundle.json"
    values[_relative(active_bundle)] = file_sha256(active_bundle) if active_bundle.exists() else None
    return values


def _assert_context() -> tuple[dict[str, Any], pd.DataFrame]:
    if file_sha256(P1_MANIFEST) != EXPECTED_P1_MANIFEST_SHA:
        raise P2ProtocolError("P1 manifest bytes changed")
    if file_sha256(PREREG) != EXPECTED_PREREG_SHA:
        raise P2ProtocolError("accepted P2 preregistration bytes changed")
    prereg = _json(PREREG)
    if prereg.get("status") != "accepted":
        raise P2ProtocolError("P2 preregistration is not accepted")
    economics = prereg["economics"]
    if (
        economics.get("actual_cost_pressure_total") != ACTUAL_COST_PRESSURE_TOTAL
        or economics.get("additional_slippage_round_trip")
        != ADDITIONAL_SLIPPAGE_ROUND_TRIP
        or economics.get("funding_adjustment") != "not_modeled_p1_only"
    ):
        raise P2ProtocolError("P2 cost pressure line differs from Owner approval")
    selector = prereg["selector"]
    if selector.get("calibration_quantile") != 0.9 or selector.get("threshold_operator") != ">=":
        raise P2ProtocolError("P2 selector differs from Owner approval")
    frame = load_immutable_dataset(P1_MANIFEST)
    if file_sha256(PROJECT / prereg["dataset"]["path"]) != EXPECTED_DATASET_SHA:
        raise P2ProtocolError("P1 dataset bytes changed")
    return prereg, frame


def _segment(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "rows": int(len(frame)),
        "event_groups": int(frame["event_group_id"].nunique()),
        "symbols": int(frame["symbol"].nunique()),
        "signal_start": str(pd.to_datetime(frame["signal_time"], utc=True).min()),
        "signal_end": str(pd.to_datetime(frame["signal_time"], utc=True).max()),
        "max_interval_end": str(pd.to_datetime(frame["interval_end"], utc=True).max()),
    }


def _model_health(model: Any, scores: np.ndarray, gate: dict[str, Any]) -> dict[str, Any]:
    dump = model.dump_model()
    leaf_counts = [int(tree["num_leaves"]) for tree in dump.get("tree_info", [])]
    checks = {
        "best_iteration_gt_1": int(model.best_iteration) > 1,
        **{str(key): bool(value) for key, value in gate["health_checks"].items()},
    }
    return {
        "best_iteration": int(model.best_iteration),
        "tree_count": int(model.num_trees()),
        "tree_leaf_count_min": min(leaf_counts) if leaf_counts else 0,
        "tree_leaf_count_total": int(sum(leaf_counts)),
        "distinct_scores": int(np.unique(scores).size),
        "checks": checks,
        "accepted": bool(all(checks.values())),
    }


def _selector_view(gate: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in gate.items() if key != "health_checks"}


def _weighted_rank_aggregate(fold_results: list[dict[str, Any]], *, baseline: bool) -> dict[str, Any]:
    rows = np.asarray([fold["segments"]["test"]["rows"] for fold in fold_results], dtype=float)
    ranks = [
        fold["single_feature_baseline"]["test"]["rank"]
        if baseline
        else fold["test"]["rank"]
        for fold in fold_results
    ]
    keys = ("roc_auc", "pr_auc", "spearman_score_vs_net_taker")
    return {
        "aggregation": "test-row-weighted mean of per-fold rank metrics; raw scores are not pooled across models",
        "fold_values": [{"fold": fold["fold"], **rank} for fold, rank in zip(fold_results, ranks)],
        **{
            key: float(np.average([rank[key] for rank in ranks], weights=rows))
            for key in keys
        },
    }


def _weighted_exact_top_aggregate(fold_results: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = [fold["test"]["exact_top_decile"] for fold in fold_results]
    weights = np.asarray([metric["effective_n"] for metric in metrics], dtype=float)
    total_test = sum(fold["segments"]["test"]["rows"] for fold in fold_results)
    keys = (
        "mean_gross_ret",
        "mean_net_taker",
        "mean_pressure_net",
        "win_rate_tp_before_sl",
    )
    return {
        "aggregation": "effective-top-decile-n weighted mean of per-fold diagnostics; raw scores are not pooled across models",
        "n": int(sum(metric["n"] for metric in metrics)),
        "effective_n": float(weights.sum()),
        "pass_rate": float(weights.sum() / total_test),
        **{
            key: float(np.average([metric[key] for metric in metrics], weights=weights))
            for key in keys
        },
        "pressure_profit_factor": None,
        "pressure_profit_factor_note": "not reconstructable from fold summaries; per-fold PFs remain authoritative",
        "approved_total_cost": ACTUAL_COST_PRESSURE_TOTAL,
        "additional_slippage_deducted": ADDITIONAL_SLIPPAGE_ROUND_TRIP,
        "positive_folds": int(sum(metric["mean_pressure_net"] > 0 for metric in metrics)),
        "fold_values": [
            {
                "fold": fold["fold"],
                "n": metric["n"],
                "mean_pressure_net": metric["mean_pressure_net"],
                "pressure_profit_factor": metric["pressure_profit_factor"],
            }
            for fold, metric in zip(fold_results, metrics)
        ],
    }


def run_fixture() -> dict[str, Any]:
    before = _protected_hashes()
    scores = np.arange(200, dtype=float)
    gate = calibrate_runtime_gate(scores)
    top = exact_top_fraction_weights(np.array([0.0] * 16 + [1.0] * 4))
    tied_gate = calibrate_runtime_gate(np.array([0.0] * 160 + [1.0] * 40))
    pairs = pd.DataFrame(
        [
            {
                "utc_week": f"2026-W{week:02d}",
                "selected_pressure_net": 0.01,
                "control_pressure_net": 0.0,
            }
            for week in range(1, 9)
        ]
    )
    permutation = exact_block_signflip_pvalue(pairs)
    checks = {
        "separable_q90_selects_exact_10pct": gate["actual_selected_n"] == 20,
        "separable_gate_health": gate["health_accepted"] is True,
        "fractional_tie_weight_is_equal": top["equality_weight"] == 0.5,
        "tied_runtime_gate_never_slices": tied_gate["actual_selected_n"] == 40,
        "tied_runtime_gate_fails_pass_health": tied_gate["health_accepted"] is False,
        "economic_block_permutation_exact": permutation["permutations"] == 256,
        "economic_block_permutation_p_lt_001": permutation["p_value"] < 0.01,
    }
    after = _protected_hashes()
    checks["protected_unchanged"] = before == after
    payload = {
        "phase": "P2 fixture",
        "generated_at": _now(),
        "prereg_sha256": file_sha256(PREREG),
        "checks": checks,
        "selector_fixture": _selector_view(gate),
        "tied_selector_fixture": _selector_view(tied_gate),
        "permutation_fixture": permutation,
        "protected_before": before,
        "protected_after": after,
        "accepted": bool(all(checks.values())),
        "safety": {
            "real_dataset_read": False,
            "trained": False,
            "holdout_read": False,
            "active_modified": False,
            "deployed": False,
            "ordered": False,
        },
    }
    _write_json(FIXTURE, payload)
    return payload


def run_dry_run() -> dict[str, Any]:
    prereg, frame = _assert_context()
    before = _protected_hashes()
    split = prepare_three_way_split(frame)
    train = split.train.iloc[:1500].copy()
    early = split.early_stop.iloc[:600].copy()
    calibration = split.calibration.iloc[:600].copy()
    model = train_regressor(
        train,
        early,
        num_boost_round=60,
        early_stopping_rounds=10,
    )
    calibration_scores = predict(model, calibration)
    gate = calibrate_runtime_gate(calibration_scores)
    metrics, mask = evaluate_fixed_gate(calibration, calibration_scores, gate)
    baseline = fit_single_feature_baseline(train)
    baseline_scores = predict_single_feature(baseline, calibration)
    baseline_gate = calibrate_runtime_gate(baseline_scores)
    baseline_metrics, _ = evaluate_fixed_gate(calibration, baseline_scores, baseline_gate)
    pairs = matched_candidate_pairs(calibration, mask)
    permutation = exact_block_signflip_pvalue(pairs)
    after = _protected_hashes()
    checks = {
        "accepted_prereg": prereg["status"] == "accepted",
        "fixed_sample_sizes": [len(train), len(early), len(calibration)] == [1500, 600, 600],
        "finite_scores": bool(np.isfinite(calibration_scores).all()),
        "runtime_gate_identity": bool(
            np.array_equal(mask, apply_runtime_gate(calibration_scores, threshold=gate["threshold"]))
        ),
        "protected_unchanged": before == after,
    }
    payload = {
        "phase": "P2 small-sample dry-run",
        "generated_at": _now(),
        "dataset_sha256": EXPECTED_DATASET_SHA,
        "prereg_sha256": file_sha256(PREREG),
        "sample": {
            "train": _segment(train),
            "early_stop": _segment(early),
            "calibration": _segment(calibration),
        },
        "model_health_observed_not_acceptance_gate": _model_health(
            model, calibration_scores, gate
        ),
        "selector": _selector_view(gate),
        "metrics": metrics,
        "single_feature_baseline": {
            "selector": _selector_view(baseline_gate),
            "metrics": baseline_metrics,
        },
        "matched_control": {
            "pairs": int(len(pairs)),
            "permutation": permutation,
        },
        "checks": checks,
        "protected_before": before,
        "protected_after": after,
        "accepted": bool(all(checks.values())),
        "safety": {
            "data_sources_used": [_relative(PROJECT / prereg["dataset"]["path"])],
            "holdout_read": False,
            "active_modified": False,
            "active_bundle_created": False,
            "deployed": False,
            "ordered": False,
        },
    }
    _write_json(DRY_RUN, payload)
    return payload


def _fold_result(fold: Any) -> tuple[dict[str, Any], pd.DataFrame, dict[str, np.ndarray]]:
    model = train_regressor(fold.train, fold.early_stop)
    calibration_scores = predict(model, fold.calibration)
    gate = calibrate_runtime_gate(calibration_scores)
    test_scores = predict(model, fold.test)
    metrics, mask = evaluate_fixed_gate(fold.test, test_scores, gate)
    pairs = matched_candidate_pairs(fold.test, mask)
    pairs["fold"] = int(fold.fold)

    baseline = fit_single_feature_baseline(fold.train)
    baseline_calibration_scores = predict_single_feature(baseline, fold.calibration)
    baseline_gate = calibrate_runtime_gate(baseline_calibration_scores)
    baseline_test_scores = predict_single_feature(baseline, fold.test)
    baseline_metrics, baseline_mask = evaluate_fixed_gate(
        fold.test, baseline_test_scores, baseline_gate
    )
    identity = np.array_equal(
        mask, apply_runtime_gate(test_scores, threshold=float(gate["threshold"]))
    )
    health = _model_health(model, calibration_scores, gate)
    health_checks = {
        "model_and_selector_health": health["accepted"],
        "test_selected_floor_100": int(mask.sum()) >= 100,
        "runtime_set_identity": bool(identity),
    }
    result = {
        "fold": int(fold.fold),
        "boundaries": {
            "early_stop_start": fold.early_stop_start.isoformat(),
            "calibration_start": fold.calibration_start.isoformat(),
            "test_start": fold.test_start.isoformat(),
            "test_end": fold.test_end.isoformat(),
        },
        "segments": {
            "train": _segment(fold.train),
            "early_stop": _segment(fold.early_stop),
            "calibration": _segment(fold.calibration),
            "test": _segment(fold.test),
            "purged_rows": int(len(fold.purged)),
        },
        "model_health": health,
        "selector": _selector_view(gate),
        "test": metrics,
        "matched_control": {
            "selected_n": int(mask.sum()),
            "pairs": int(len(pairs)),
            "coverage": float(len(pairs) / mask.sum()) if mask.sum() else 0.0,
            "selected_pressure_net": float(pairs["selected_pressure_net"].mean())
            if len(pairs)
            else None,
            "control_pressure_net": float(pairs["control_pressure_net"].mean())
            if len(pairs)
            else None,
            "lift": float(
                (pairs["selected_pressure_net"] - pairs["control_pressure_net"]).mean()
            )
            if len(pairs)
            else None,
        },
        "single_feature_baseline": {
            "selector": _selector_view(baseline_gate),
            "test": baseline_metrics,
        },
        "health_checks": health_checks,
        "health_accepted": bool(all(health_checks.values())),
    }
    arrays = {
        "test_scores": test_scores,
        "test_mask": mask,
        "baseline_scores": baseline_test_scores,
        "baseline_mask": baseline_mask,
    }
    return result, pairs, arrays


def run_full() -> dict[str, Any]:
    prereg, frame = _assert_context()
    fixture = _json(FIXTURE)
    dry_run = _json(DRY_RUN)
    if not fixture.get("accepted") or not dry_run.get("accepted"):
        raise P2ProtocolError("fixture and dry-run must both be accepted before full")
    if fixture.get("prereg_sha256") != EXPECTED_PREREG_SHA or dry_run.get(
        "prereg_sha256"
    ) != EXPECTED_PREREG_SHA:
        raise P2ProtocolError("fixture/dry-run do not bind the accepted preregistration")
    before = _protected_hashes()
    split = prepare_three_way_split(frame)
    model = train_regressor(split.train, split.early_stop)
    calibration_scores = predict(model, split.calibration)
    gate = calibrate_runtime_gate(calibration_scores)
    calibration_metrics, calibration_mask = evaluate_fixed_gate(
        split.calibration, calibration_scores, gate
    )
    main_health = _model_health(model, calibration_scores, gate)

    MODEL.parent.mkdir(parents=True, exist_ok=True)
    model.save_model(str(MODEL), num_iteration=model.best_iteration)
    model_sha = file_sha256(MODEL)
    importance = pd.DataFrame(
        {
            "feature": list(FEATURE_COLUMNS),
            "gain": model.feature_importance(importance_type="gain"),
            "split": model.feature_importance(importance_type="split"),
        }
    ).sort_values(["gain", "feature"], ascending=[False, True])
    importance.to_csv(IMPORTANCE, index=False)

    baseline = fit_single_feature_baseline(split.train)
    baseline_calibration_scores = predict_single_feature(baseline, split.calibration)
    baseline_gate = calibrate_runtime_gate(baseline_calibration_scores)
    baseline_calibration_metrics, _ = evaluate_fixed_gate(
        split.calibration, baseline_calibration_scores, baseline_gate
    )

    fold_results: list[dict[str, Any]] = []
    pair_frames: list[pd.DataFrame] = []
    oos_frames: list[pd.DataFrame] = []
    oos_scores: list[np.ndarray] = []
    oos_masks: list[np.ndarray] = []
    baseline_scores: list[np.ndarray] = []
    baseline_masks: list[np.ndarray] = []
    for fold in walkforward_folds(frame):
        result, pairs, arrays = _fold_result(fold)
        fold_results.append(result)
        pair_frames.append(pairs)
        oos_frames.append(fold.test)
        oos_scores.append(arrays["test_scores"])
        oos_masks.append(arrays["test_mask"])
        baseline_scores.append(arrays["baseline_scores"])
        baseline_masks.append(arrays["baseline_mask"])

    combined = pd.concat(oos_frames, ignore_index=True)
    combined_scores = np.concatenate(oos_scores)
    combined_mask = np.concatenate(oos_masks)
    combined_baseline_scores = np.concatenate(baseline_scores)
    combined_baseline_mask = np.concatenate(baseline_masks)
    aggregate_metrics = {
        "rank": _weighted_rank_aggregate(fold_results, baseline=False),
        "exact_top_decile": _weighted_exact_top_aggregate(fold_results),
        "fixed_gate": economic_metrics(combined, combined_scores, selected_mask=combined_mask),
    }
    aggregate_baseline = {
        "rank": _weighted_rank_aggregate(fold_results, baseline=True),
        "fixed_gate": economic_metrics(
            combined,
            combined_baseline_scores,
            selected_mask=combined_baseline_mask,
        ),
    }
    all_pairs = pd.concat(pair_frames, ignore_index=True) if pair_frames else pd.DataFrame()
    all_pairs.to_csv(MATCHED_PAIRS, index=False)
    permutation = exact_block_signflip_pvalue(all_pairs)
    matched = {
        "selected_n": int(combined_mask.sum()),
        "pairs": int(len(all_pairs)),
        "coverage": float(len(all_pairs) / combined_mask.sum()) if combined_mask.sum() else 0.0,
        "selected_pressure_net": float(all_pairs["selected_pressure_net"].mean())
        if len(all_pairs)
        else None,
        "control_pressure_net": float(all_pairs["control_pressure_net"].mean())
        if len(all_pairs)
        else None,
        "lift": float(
            (all_pairs["selected_pressure_net"] - all_pairs["control_pressure_net"]).mean()
        )
        if len(all_pairs)
        else None,
        "permutation": permutation,
        "pairs_path": _relative(MATCHED_PAIRS),
        "pairs_sha256": file_sha256(MATCHED_PAIRS),
    }

    positive_folds = sum(
        result["test"]["fixed_gate"].get("mean_pressure_net", float("-inf")) > 0
        for result in fold_results
    )
    runtime_identity = bool(
        np.array_equal(
            calibration_mask,
            apply_runtime_gate(calibration_scores, threshold=float(gate["threshold"])),
        )
    )
    success_gates = {
        "main_model_and_selector_health": bool(main_health["accepted"]),
        "calibration_selected_n_gte_300": int(calibration_mask.sum()) >= 300,
        "all_five_walkforward_health_gates": bool(
            len(fold_results) == 5 and all(result["health_accepted"] for result in fold_results)
        ),
        "walkforward_positive_fixed_gate_folds_gte_4": positive_folds >= 4,
        "aggregate_fixed_gate_pressure_net_mean_gt_0": aggregate_metrics["fixed_gate"].get(
            "mean_pressure_net", float("-inf")
        )
        > 0,
        "matched_lift_gt_0": matched["lift"] is not None and matched["lift"] > 0,
        "economic_block_permutation_p_lt_0_01": permutation["p_value"] is not None
        and permutation["p_value"] < 0.01,
        "offline_runtime_set_identity": runtime_identity,
        "arbitrary_tie_slicing": False,
    }
    # The final entry is a required invariant whose passing state is False as a
    # fact (no arbitrary slicing occurred), unlike the positive booleans above.
    decision_checks = {
        key: value for key, value in success_gates.items() if key != "arbitrary_tie_slicing"
    }
    decision_checks["no_arbitrary_tie_slicing"] = success_gates["arbitrary_tie_slicing"] is False
    verdict = "accepted" if all(decision_checks.values()) else "rejected"

    selector_manifest = {
        "manifest_version": "p2_research_selector_manifest_v1",
        "generated_at": _now(),
        "p2_verdict": verdict,
        "research_only": True,
        "execution_eligible": False,
        "promotion_eligible": False,
        "dataset_path": prereg["dataset"]["path"],
        "dataset_sha256": EXPECTED_DATASET_SHA,
        "preregistration_path": _relative(PREREG),
        "preregistration_sha256": EXPECTED_PREREG_SHA,
        "model_path": _relative(MODEL),
        "model_sha256": model_sha,
        "model_objective": "regression",
        "target_column": TARGET_COLUMN,
        "target_semantics": "net_taker",
        "target_cost_included": True,
        "model_num_iteration": int(model.best_iteration),
        "feature_columns": list(FEATURE_COLUMNS),
        "feature_semantics": "side_aligned_v1",
        "calibration_quantile": gate["calibration_quantile"],
        "threshold": gate["threshold"],
        "threshold_operator": gate["threshold_operator"],
        "tie_policy": gate["tie_policy"],
        "calibration_pass_rate": gate["actual_pass_rate"],
        "threshold_equal_rate": gate["threshold_equal_rate"],
        "distinct_score_count": gate["distinct_score_count"],
        "selector_health_accepted": gate["health_accepted"],
        "actual_cost_pressure_total": ACTUAL_COST_PRESSURE_TOTAL,
        "additional_slippage_round_trip": ADDITIONAL_SLIPPAGE_ROUND_TRIP,
        "funding": "not_modeled_p1_only",
        "forbidden_activation_note": "This is not models/active_bundle.json and does not authorize ACTIVE or execution.",
    }
    _write_json(SELECTOR, selector_manifest)
    binding = {
        "binding_version": "p2_dataset_binding_v1",
        "dataset_path": prereg["dataset"]["path"],
        "dataset_sha256": EXPECTED_DATASET_SHA,
        "p1_manifest_path": _relative(P1_MANIFEST),
        "p1_manifest_sha256": EXPECTED_P1_MANIFEST_SHA,
        "p2_preregistration_path": _relative(PREREG),
        "p2_preregistration_sha256": EXPECTED_PREREG_SHA,
        "feature_schema": prereg["dataset"]["feature_schema"],
        "feature_semantics": prereg["dataset"]["feature_semantics"],
        "row_count": int(len(frame)),
        "holdout_rows": 0,
        "data_sources_used": [prereg["dataset"]["path"]],
    }
    _write_json(BINDING, binding)
    after = _protected_hashes()
    protected_unchanged = before == after
    if not protected_unchanged:
        raise P2ProtocolError("a protected runtime artifact changed during P2")

    payload = {
        "result_version": "p2_l2_preholdout_validation_v1",
        "generated_at": _now(),
        "verdict": verdict,
        "evidence_class": "preholdout_research_only_not_deployment_or_holdout_proof",
        "dataset_binding": binding,
        "preregistration": {
            "path": _relative(PREREG),
            "sha256": EXPECTED_PREREG_SHA,
            "owner_approval": prereg["owner_approval"],
        },
        "cost": {
            "p1_taker_round_trip": 0.001,
            "additional_slippage_round_trip": ADDITIONAL_SLIPPAGE_ROUND_TRIP,
            "actual_cost_pressure_total": ACTUAL_COST_PRESSURE_TOTAL,
            "funding": "not_modeled_p1_only",
            "double_cost_applied": False,
        },
        "main": {
            "segments": {
                "train": _segment(split.train),
                "early_stop": _segment(split.early_stop),
                "calibration": _segment(split.calibration),
                "purged_rows": int(len(split.purged)),
            },
            "model_path": _relative(MODEL),
            "model_sha256": model_sha,
            "model_health": main_health,
            "selector": _selector_view(gate),
            "calibration": calibration_metrics,
            "single_feature_baseline": {
                "selector": _selector_view(baseline_gate),
                "calibration": baseline_calibration_metrics,
            },
            "feature_importance_path": _relative(IMPORTANCE),
            "feature_importance_sha256": file_sha256(IMPORTANCE),
            "feature_importance_top10": importance.head(10).to_dict("records"),
        },
        "walkforward": {
            "test_folds": 5,
            "positive_fixed_gate_folds": int(positive_folds),
            "folds": fold_results,
            "aggregate": aggregate_metrics,
            "single_feature_baseline_aggregate": aggregate_baseline,
            "matched_control": matched,
        },
        "success_gates": decision_checks,
        "selector_manifest_path": _relative(SELECTOR),
        "selector_manifest_sha256": file_sha256(SELECTOR),
        "protected_before": before,
        "protected_after": after,
        "protected_unchanged": protected_unchanged,
        "safety": {
            "data_sources_used": [prereg["dataset"]["path"]],
            "holdout_read": False,
            "active_modified": False,
            "active_bundle_created": False,
            "deployed": False,
            "trading_client_accessed": False,
            "ordered": False,
        },
    }
    _write_json(RESULTS, payload)
    return payload


def finalize_existing_results() -> dict[str, Any]:
    """Correct fold aggregation from already-frozen full-run summaries; no retraining."""
    _assert_context()
    before = _protected_hashes()
    payload = _json(RESULTS)
    if payload.get("result_version") != "p2_l2_preholdout_validation_v1":
        raise P2ProtocolError("unexpected P2 results version")
    fold_results = payload["walkforward"]["folds"]
    old_rank = payload["walkforward"]["aggregate"]["rank"]
    old_exact = payload["walkforward"]["aggregate"]["exact_top_decile"]
    payload["walkforward"]["aggregate"]["rank"] = _weighted_rank_aggregate(
        fold_results, baseline=False
    )
    payload["walkforward"]["aggregate"]["exact_top_decile"] = (
        _weighted_exact_top_aggregate(fold_results)
    )
    payload["walkforward"]["single_feature_baseline_aggregate"]["rank"] = (
        _weighted_rank_aggregate(fold_results, baseline=True)
    )
    payload["aggregation_correction"] = {
        "corrected_at": _now(),
        "training_rerun": False,
        "reason": "raw scores from different fold models are not comparable and must not be pooled for rank/top-decile",
        "old_invalid_pooled_rank": old_rank,
        "old_invalid_pooled_exact_top_decile": old_exact,
        "affected_success_gates": [],
        "verdict_unchanged": payload["verdict"],
    }
    after = _protected_hashes()
    if before != after:
        raise P2ProtocolError("a protected artifact changed during result finalization")
    _write_json(RESULTS, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("fixture", "dry-run", "full", "finalize"))
    args = parser.parse_args()
    if args.phase == "fixture":
        payload = run_fixture()
    elif args.phase == "dry-run":
        payload = run_dry_run()
    elif args.phase == "full":
        payload = run_full()
    else:
        payload = finalize_existing_results()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
