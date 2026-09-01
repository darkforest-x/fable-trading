#!/usr/bin/env python3
"""Retrain the frozen 15m L2 gross-return regressor separately by side.

Inputs are the byte-pinned dataset and matched controls from
``exp-15m-ma-launch-l2-global-context-v1``.  The source rows already contain the
28 causal features evaluated at the final L1-visible bar and TP5/SL2/72 future
outcomes.  This script never reads OHLCV or holdout data.  It preserves the
source chronological split and full-exposure dependency representatives.

The only experimental variable is model sharing: one mixed LONG/SHORT model
versus one LONG and one SHORT model.  Per-side predictions are calibrated to
their empirical tune-score percentile before aggregate ranking, so a raw-score
scale difference cannot silently favor one side.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import lightgbm as lgb
import numpy as np
import pandas as pd

from scripts.research_15m_ma_launch_l2_global_context import (
    L2_DETERMINISTIC_PARAMS,
    matched_control_metrics,
    outcome_permutation_pvalue,
    safe_metrics,
    selected_metrics,
)
from yoyo.layers.l2_judgment.features import FEATURE_COLUMNS
from yoyo.layers.l2_judgment.train import LGB_PARAMS, train_model


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-15m-ma-launch-l2-side-split-v1"
EXPERIMENT_DIR = ROOT / "experiments" / "active" / EXPERIMENT_ID
PREREG_PATH = EXPERIMENT_DIR / "preregistration.json"
RESULTS_DIR = EXPERIMENT_DIR / "results"
OUTPUT_DIR = ROOT / "analysis" / "output" / "ma_launch_l2_side_split_v1"
SIDES = ("long", "short")
LEARNING_SPLITS = ("train", "tune", "final_validation")
SEED = 42


class SideSplitError(RuntimeError):
    """Raised when a frozen experiment contract is not satisfied."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repo_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def repo_relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def load_preregistration(path: Path = PREREG_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("experiment_id") != EXPERIMENT_ID:
        raise SideSplitError("preregistration experiment_id mismatch")
    if payload["source"].get("holdout_rows") != 0:
        raise SideSplitError("source contract is not pre-holdout only")
    if payload["frozen_contract"].get("holdout_read") is not False:
        raise SideSplitError("holdout read must remain disabled")
    return payload


def verify_file(path: Path, expected_sha256: str, label: str) -> None:
    if not path.is_file():
        raise SideSplitError(f"missing {label}: {path}")
    actual = sha256_file(path)
    if actual != expected_sha256:
        raise SideSplitError(f"{label} SHA drifted: {actual} != {expected_sha256}")


def bool_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.astype(bool)
    normalized = series.astype(str).str.strip().str.lower()
    if not normalized.isin(("true", "false")).all():
        raise SideSplitError("dependency_representative contains non-boolean values")
    return normalized.map({"true": True, "false": False}).astype(bool)


def validate_source_dataset(data: pd.DataFrame, expected_rows: int) -> pd.DataFrame:
    required = {
        "episode_id",
        "side",
        "split",
        "dependency_block_id",
        "dependency_representative",
        "label",
        "realized_ret",
        "net_ret",
        *FEATURE_COLUMNS,
    }
    missing = sorted(required - set(data.columns))
    if missing:
        raise SideSplitError(f"source dataset missing columns: {missing}")
    if len(data) != int(expected_rows):
        raise SideSplitError(f"source row count drifted: {len(data)} != {expected_rows}")
    if data["episode_id"].duplicated().any():
        raise SideSplitError("source episode_id is not unique")
    sides = set(data["side"].astype(str))
    if sides != set(SIDES):
        raise SideSplitError(f"source sides drifted: {sorted(sides)}")
    splits = set(data["split"].astype(str))
    if splits != {"train", "purge", "tune", "final_validation"}:
        raise SideSplitError(f"source splits drifted: {sorted(splits)}")
    featured = data[list(FEATURE_COLUMNS)].apply(pd.to_numeric, errors="coerce")
    learning = data["split"].isin(LEARNING_SPLITS)
    if not np.isfinite(featured.loc[learning].to_numpy(dtype=float)).all():
        raise SideSplitError("learning rows contain non-finite feature values")
    out = data.copy()
    out["dependency_representative"] = bool_series(out["dependency_representative"])
    return out


def split_dataset_by_side(data: pd.DataFrame) -> dict[str, pd.DataFrame]:
    partitions = {
        side: data[data["side"].astype(str) == side].copy().reset_index(drop=True)
        for side in SIDES
    }
    ids = [set(frame["episode_id"].astype(str)) for frame in partitions.values()]
    if ids[0] & ids[1]:
        raise SideSplitError("LONG and SHORT partitions overlap")
    if ids[0] | ids[1] != set(data["episode_id"].astype(str)):
        raise SideSplitError("LONG/SHORT partitions do not exactly cover source rows")
    return partitions


def learning_rows(data: pd.DataFrame, split: str) -> pd.DataFrame:
    return data[
        (data["split"].astype(str) == split) & data["dependency_representative"]
    ].copy()


def empirical_percentile(reference: np.ndarray, values: np.ndarray) -> np.ndarray:
    """Map values to the inclusive empirical CDF of a frozen tune distribution."""
    reference = np.sort(np.asarray(reference, dtype=float))
    values = np.asarray(values, dtype=float)
    if reference.size == 0 or not np.isfinite(reference).all() or not np.isfinite(values).all():
        raise SideSplitError("percentile calibration requires finite, non-empty arrays")
    return np.searchsorted(reference, values, side="right") / reference.size


def runtime_versions() -> dict[str, Any]:
    import lightgbm
    import sklearn
    import scipy

    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": {
            "lightgbm": lightgbm.__version__,
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scikit-learn": sklearn.__version__,
            "scipy": scipy.__version__,
        },
    }


def train_arm(
    data: pd.DataFrame,
    controls: pd.DataFrame,
    *,
    arm: str,
    feature_columns: Sequence[str],
    cost: float,
    output_dir: Path,
) -> tuple[dict[str, Any], pd.DataFrame, np.ndarray, lgb.Booster]:
    subset = data if arm == "mixed" else data[data["side"].astype(str) == arm]
    train = learning_rows(subset, "train")
    tune = learning_rows(subset, "tune")
    final_events = subset[subset["split"].astype(str) == "final_validation"].copy()
    final = final_events[final_events["dependency_representative"]].copy()
    if min(len(train), len(tune), len(final)) == 0:
        raise SideSplitError(f"{arm} has an empty train/tune/final representative split")

    model = train_model(
        train,
        tune,
        feature_columns=feature_columns,
        objective="regression",
        params_override=L2_DETERMINISTIC_PARAMS,
    )
    tune_score = model.predict(tune[list(feature_columns)], num_iteration=model.best_iteration)
    threshold = float(np.quantile(tune_score, 0.9))
    event_score = model.predict(
        final_events[list(feature_columns)], num_iteration=model.best_iteration
    )
    scored = final_events.copy()
    scored["l2_score"] = event_score
    scored["side_percentile_score"] = empirical_percentile(tune_score, event_score)
    scored["l2_threshold"] = threshold
    scored["l2_keep"] = event_score >= threshold
    final = scored[scored["dependency_representative"]].copy()
    scores = final["l2_score"].to_numpy(dtype=float)
    returns = final["realized_ret"].to_numpy(dtype=float)
    labels = final["label"].to_numpy(dtype=int)
    metrics = safe_metrics(labels, scores, returns, cost)
    selection = final["l2_keep"].to_numpy(dtype=bool)
    selected = selected_metrics(final, selection, cost)
    selected_ids = set(final.loc[selection, "episode_id"].astype(str))
    arm_controls = controls if arm == "mixed" else controls[controls["side"] == arm]
    matched = matched_control_metrics(
        final,
        arm_controls,
        selected_ids,
        required_assignments=8,
    )
    pvalue = outcome_permutation_pvalue(scores, returns)
    model_path = output_dir / "models" / f"l2_{arm}_{len(feature_columns)}f.txt"
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model.save_model(str(model_path))
    importance = pd.DataFrame(
        {
            "feature": list(feature_columns),
            "gain": model.feature_importance(importance_type="gain"),
            "split": model.feature_importance(importance_type="split"),
        }
    ).sort_values("gain", ascending=False)
    importance_path = output_dir / f"feature_importance_{arm}_{len(feature_columns)}f.csv"
    importance.to_csv(importance_path, index=False)
    summary = {
        "arm": arm,
        "feature_columns": list(feature_columns),
        "splits": {"train": len(train), "tune": len(tune), "final_validation": len(final)},
        "split_event_counts": {
            split: int(((subset["split"] == split)).sum()) for split in LEARNING_SPLITS
        },
        "best_iteration": int(model.best_iteration),
        "tune_score_q90_threshold": threshold,
        "final_validation": metrics,
        "final_validation_frozen_threshold": selected,
        "outcome_permutation_p": pvalue,
        "matched_control": matched,
        "model_path": repo_relative(model_path),
        "model_sha256": sha256_file(model_path),
        "feature_importance_path": repo_relative(importance_path),
        "feature_importance_sha256": sha256_file(importance_path),
        "feature_importance_top10": importance.head(10).to_dict("records"),
    }
    return summary, scored, tune_score, model


def aggregate_side_metrics(
    scored_by_side: Mapping[str, pd.DataFrame],
    controls: pd.DataFrame,
    *,
    cost: float,
) -> dict[str, Any]:
    scored = pd.concat([scored_by_side[side] for side in SIDES], ignore_index=True)
    final = scored[scored["dependency_representative"]].copy()
    score = final["side_percentile_score"].to_numpy(dtype=float)
    returns = final["realized_ret"].to_numpy(dtype=float)
    labels = final["label"].to_numpy(dtype=int)
    keep = final["l2_keep"].to_numpy(dtype=bool)
    selected_ids = set(final.loc[keep, "episode_id"].astype(str))
    return {
        "final_validation": safe_metrics(labels, score, returns, cost),
        "final_validation_frozen_threshold": selected_metrics(final, keep, cost),
        "outcome_permutation_p": outcome_permutation_pvalue(score, returns),
        "matched_control": matched_control_metrics(
            final, controls, selected_ids, required_assignments=8
        ),
        "scored": scored,
    }


def compare_prior_scores(reproduced: pd.DataFrame, prior: pd.DataFrame, tolerance: float) -> dict[str, Any]:
    left = reproduced[reproduced["dependency_representative"]][
        ["episode_id", "l2_score", "l2_keep"]
    ].copy()
    right = prior[prior["dependency_representative"]][
        ["episode_id", "l2_score", "l2_keep"]
    ].copy()
    merged = left.merge(right, on="episode_id", suffixes=("_new", "_prior"), validate="one_to_one")
    if len(merged) != len(left) or len(merged) != len(right):
        raise SideSplitError("mixed reproduction/prior final rows do not match")
    delta = np.abs(merged["l2_score_new"] - merged["l2_score_prior"])
    keep_equal = bool((merged["l2_keep_new"] == merged["l2_keep_prior"]).all())
    return {
        "rows": len(merged),
        "maximum_absolute_score_delta": float(delta.max()) if len(delta) else 0.0,
        "score_tolerance": float(tolerance),
        "scores_within_tolerance": bool((delta <= tolerance).all()),
        "keep_decisions_exact": keep_equal,
    }


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_train_evaluate(prereg: Mapping[str, Any]) -> dict[str, Any]:
    dataset_path = repo_path(prereg["source"]["dataset_path"])
    controls_path = repo_path(prereg["source"]["matched_controls_path"])
    prior_receipt_path = repo_path(prereg["source"]["prior_training_receipt"])
    prior_scored_path = repo_path(prereg["source"]["prior_scored_validation"])
    for path, sha, label in (
        (dataset_path, prereg["source"]["dataset_sha256"], "source dataset"),
        (controls_path, prereg["source"]["matched_controls_sha256"], "matched controls"),
        (prior_receipt_path, prereg["source"]["prior_training_receipt_sha256"], "prior receipt"),
        (prior_scored_path, prereg["source"]["prior_scored_validation_sha256"], "prior scores"),
        (
            repo_path(prereg["frozen_contract"]["feature_builder"]),
            prereg["frozen_contract"]["feature_builder_sha256"],
            "feature builder",
        ),
        (
            repo_path(prereg["model"]["training_builder"]),
            prereg["model"]["training_builder_sha256"],
            "training builder",
        ),
    ):
        verify_file(path, str(sha), label)
    data = validate_source_dataset(
        pd.read_csv(dataset_path), int(prereg["source"]["dataset_rows"])
    )
    controls = pd.read_csv(controls_path)
    prior = pd.read_csv(prior_scored_path)
    prior["dependency_representative"] = bool_series(prior["dependency_representative"])
    prior["l2_keep"] = bool_series(prior["l2_keep"])
    partitions = split_dataset_by_side(data)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for side, frame in partitions.items():
        frame.to_csv(OUTPUT_DIR / f"l2_dataset_{side}.csv", index=False)

    cost = float(prereg["frozen_contract"]["round_trip_cost_fraction"])
    mixed, mixed_scored, _, _ = train_arm(
        data,
        controls,
        arm="mixed",
        feature_columns=FEATURE_COLUMNS,
        cost=cost,
        output_dir=OUTPUT_DIR,
    )
    baselines: dict[str, Any] = {}
    side_results: dict[str, Any] = {}
    side_scored: dict[str, pd.DataFrame] = {}
    for arm in ("mixed", *SIDES):
        arm_data = data if arm == "mixed" else partitions[arm]
        baseline, _, _, _ = train_arm(
            arm_data,
            controls,
            arm=arm,
            feature_columns=["ma_spread_pct"],
            cost=cost,
            output_dir=OUTPUT_DIR,
        )
        baselines[arm] = baseline
        if arm != "mixed":
            result, scored, _, _ = train_arm(
                arm_data,
                controls,
                arm=arm,
                feature_columns=FEATURE_COLUMNS,
                cost=cost,
                output_dir=OUTPUT_DIR,
            )
            side_results[arm] = result
            side_scored[arm] = scored

    aggregate = aggregate_side_metrics(side_scored, controls, cost=cost)
    combined_scored = aggregate.pop("scored")
    scored_path = OUTPUT_DIR / "final_validation_side_split_scored.csv"
    combined_scored.to_csv(scored_path, index=False)
    reproduction = compare_prior_scores(
        mixed_scored,
        prior,
        float(prereg["comparison"]["score_absolute_tolerance"]),
    )
    side_selected = {
        side: side_results[side]["final_validation_frozen_threshold"] for side in SIDES
    }
    primary = {
        "mixed_reproduction": bool(
            reproduction["scores_within_tolerance"] and reproduction["keep_decisions_exact"]
        ),
        "aggregate_frozen_threshold_net_strictly_better_than_mixed": bool(
            aggregate["final_validation_frozen_threshold"]["net_mean"]
            > mixed["final_validation_frozen_threshold"]["net_mean"]
        ),
        "aggregate_top_decile_net_positive": bool(
            aggregate["final_validation"]["top_decile"]["net_mean"] > 0
        ),
        "aggregate_outcome_permutation_p_lt_0_01": bool(
            aggregate["outcome_permutation_p"] < 0.01
        ),
        "aggregate_minimum_30_selected_dependency_blocks": bool(
            aggregate["final_validation_frozen_threshold"]["n"] >= 30
        ),
        "aggregate_beats_matched_controls_every_assignment": bool(
            aggregate["matched_control"]["all_assignments_positive"]
        ),
        "each_side_minimum_10_selected_dependency_blocks": all(
            side_selected[side]["n"] >= 10 for side in SIDES
        ),
        "neither_side_frozen_threshold_net_negative": all(
            side_selected[side]["net_mean"] is not None
            and side_selected[side]["net_mean"] >= 0
            for side in SIDES
        ),
    }
    primary["passed"] = all(primary.values())
    effective_params = dict(LGB_PARAMS)
    effective_params.update(L2_DETERMINISTIC_PARAMS)
    effective_params["objective"] = "regression"
    payload = {
        "protocol": prereg["protocol"],
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": git_head(),
        "source_dataset_sha256": prereg["source"]["dataset_sha256"],
        "source_rows": len(data),
        "partition_rows": {side: len(frame) for side, frame in partitions.items()},
        "partition_split_counts": {
            side: {str(k): int(v) for k, v in frame["split"].value_counts().items()}
            for side, frame in partitions.items()
        },
        "objective": "regression_gross_realized_ret",
        "training_params": effective_params,
        "runtime": runtime_versions(),
        "mixed_reproduction": reproduction,
        "mixed": mixed,
        "sides": side_results,
        "aggregate_side_split": aggregate,
        "single_feature_baselines": baselines,
        "primary_gate": primary,
        "scored_validation_path": repo_relative(scored_path),
        "scored_validation_sha256": sha256_file(scored_path),
        "partition_paths": {
            side: {
                "path": repo_relative(OUTPUT_DIR / f"l2_dataset_{side}.csv"),
                "sha256": sha256_file(OUTPUT_DIR / f"l2_dataset_{side}.csv"),
            }
            for side in SIDES
        },
        "holdout_consumed": False,
        "promoted": False,
        "deployed": False,
        "active_or_frozen_changed": False,
        "forward_state_changed": False,
        "orders_placed": False,
        "telegram_sent": False,
        "production_eligible": False,
    }
    write_json(RESULTS_DIR / "training_receipt.json", payload)
    return payload


def verify_outputs(prereg: Mapping[str, Any]) -> dict[str, Any]:
    receipt_path = RESULTS_DIR / "training_receipt.json"
    if not receipt_path.is_file():
        raise SideSplitError("training receipt does not exist")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    checks: dict[str, bool] = {
        "protocol": receipt.get("protocol") == prereg["protocol"],
        "source_hash": receipt.get("source_dataset_sha256") == prereg["source"]["dataset_sha256"],
        "row_partition": sum(receipt["partition_rows"].values()) == receipt["source_rows"],
        "mixed_reproduction": bool(receipt["mixed_reproduction"]["scores_within_tolerance"]),
        "mixed_keep_reproduction": bool(receipt["mixed_reproduction"]["keep_decisions_exact"]),
        "no_holdout": receipt.get("holdout_consumed") is False,
        "no_production_mutation": all(
            receipt.get(key) is False
            for key in (
                "promoted",
                "deployed",
                "active_or_frozen_changed",
                "forward_state_changed",
                "orders_placed",
                "telegram_sent",
                "production_eligible",
            )
        ),
    }
    for side in SIDES:
        item = receipt["partition_paths"][side]
        path = repo_path(item["path"])
        checks[f"{side}_partition_hash"] = path.is_file() and sha256_file(path) == item["sha256"]
        model = repo_path(receipt["sides"][side]["model_path"])
        checks[f"{side}_model_hash"] = model.is_file() and sha256_file(model) == receipt["sides"][side]["model_sha256"]
    scored = repo_path(receipt["scored_validation_path"])
    checks["scored_hash"] = scored.is_file() and sha256_file(scored) == receipt["scored_validation_sha256"]
    training_generated_at = str(receipt.get("generated_at", ""))
    if not training_generated_at:
        raise SideSplitError("training receipt is missing generated_at")
    payload = {
        "protocol": prereg["protocol"],
        "experiment_id": EXPERIMENT_ID,
        # Verification is a pure integrity check.  Anchor its receipt to the
        # immutable training receipt so rerunning --verify does not silently
        # change the registered artifact hash.
        "generated_at": training_generated_at,
        "training_receipt_sha256": sha256_file(receipt_path),
        "checks": checks,
        "passed": all(checks.values()),
    }
    write_json(RESULTS_DIR / "verify_receipt.json", payload)
    if not payload["passed"]:
        raise SideSplitError(f"verification failed: {checks}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train-evaluate", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.train_evaluate == args.verify:
        parser.error("choose exactly one of --train-evaluate or --verify")
    prereg = load_preregistration()
    payload = run_train_evaluate(prereg) if args.train_evaluate else verify_outputs(prereg)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
