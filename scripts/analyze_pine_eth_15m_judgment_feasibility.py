#!/usr/bin/env python3
"""Capacity audit for a future Pine-specific LR/LightGBM judgment layer.

This script fits and scores nothing.  It reads only the training-ineligible
2023/2024 lineage manifest and reports class counts, fold event counts, and
transparent events-per-effective-feature planning scenarios.  These scenarios
are design stress tests, not universal adequacy rules or performance promises.

The purpose is to prevent a 28-feature model from being trained merely because
the repository contains LightGBM.  Any future training remains blocked by P0/P1
and must run inside the stateful Pine replay after owner authorization.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
RESULTS = EXPERIMENT / "results"
MANIFEST = RESULTS / "pine_judgment_development_manifest.json"
OUTPUT = RESULTS / "judgment_feasibility.json"


def _event_count(rows: int, rate: float) -> int:
    return int(round(rows * rate))


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows = int(manifest["rows"])
    positives = int(manifest["net_positive_rows"])
    rate = float(manifest["net_positive_rate"])
    features = int(manifest["feature_count"])
    observed_years = 2.0
    observed_rows_per_year = rows / observed_years
    folds = []
    for fold in manifest["folds"]:
        train_rows = int(fold["purged_train_rows"])
        validation_rows = int(fold["validation_rows"])
        folds.append(
            {
                "fold": fold["fold"],
                "purged_train_rows": train_rows,
                "train_positive_events": _event_count(
                    train_rows, float(fold["train_positive_rate"])
                ),
                "validation_rows": validation_rows,
                "validation_positive_events": _event_count(
                    validation_rows, float(fold["validation_positive_rate"])
                ),
                "events_per_28_features_train": (
                    _event_count(train_rows, float(fold["train_positive_rate"]))
                    / features
                ),
            }
        )

    scenarios: list[dict[str, Any]] = []
    for effective_features in (1, 3, 5, 28):
        for target_events_per_feature in (5, 10, 20):
            required_positive_events = effective_features * target_events_per_feature
            required_rows = required_positive_events / rate
            scenarios.append(
                {
                    "effective_features": effective_features,
                    "planning_events_per_feature": target_events_per_feature,
                    "required_positive_events": required_positive_events,
                    "required_total_rows_at_observed_rate": required_rows,
                    "years_at_observed_eth_row_rate": required_rows / observed_rows_per_year,
                    "current_rows_meet_scenario": rows >= required_rows,
                }
            )

    payload = {
        "audit": "Pine judgment layer sample-capacity feasibility",
        "source_status": manifest["status"],
        "holdout_rows_read": 0,
        "consumed_final_rows_read": int(
            manifest["data_quality"]["consumed_final_rows_read"]
        ),
        "training_or_scoring_performed": False,
        "training_eligible": bool(manifest["training_eligible"]),
        "rows": rows,
        "positive_events": positives,
        "positive_rate": rate,
        "candidate_features": features,
        "overall_positive_events_per_feature": positives / features,
        "fold_capacity": folds,
        "planning_scenarios": scenarios,
        "planning_warning": (
            "Events-per-effective-feature is only a transparent capacity stress test; it "
            "does not prove adequacy at any threshold and does not replace learning curves, "
            "calibration, temporal validation, or economic evaluation."
        ),
        "feasible_next_model_if_later_authorized": (
            "Start with one preregistered causal feature in regularized LogisticRegression; "
            "at most a tiny prior-chosen feature set. Do not fit the 28-feature LightGBM on "
            "166 ETH trades. Expand candidates with stateful dynamic labels and/or a "
            "time-grouped cross-symbol dataset before considering the full model."
        ),
        "old_project_model_reusable": False,
        "old_project_model_failure_reason": manifest["why_old_model_is_invalid"],
        "stateful_replay_required": True,
        "counterfactual_limit": manifest["counterfactual_limit"],
    }
    if payload["training_eligible"] or payload["training_or_scoring_performed"]:
        raise RuntimeError("judgment feasibility audit must not train or enable the dataset")
    if payload["holdout_rows_read"] or payload["consumed_final_rows_read"]:
        raise RuntimeError("judgment feasibility audit used a later period")
    OUTPUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
