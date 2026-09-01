#!/usr/bin/env python3
"""Build and audit a causal L1 -> L1.5 -> side-specific L2 research pipeline.

L1 remains the frozen Grade-A native-1280 YOLO.  L1.5 is a side-specific
LightGBM classifier using 128 decision-time bars and existing Grade-A launch
versus matched dense-no-launch event labels.  L2 remains actual-return
regression with TP5/SL2/72 and 20 bp round-trip cost.  All model inputs end at
the complete L1 window close; only labels read later bars.

The economic candidate period is a previously consumed pre-holdout development
set.  Its result is therefore diagnostic and cannot promote this configuration.
No row at or after 2026-05-04 is read by this script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, precision_score, recall_score, roc_auc_score

from scripts.research_15m_ma_launch_l2_global_context import (
    CLASS_COLORS,
    normalize_ohlcv,
    normalized_box_corners,
    outcome_permutation_pvalue,
    pixel_sha256,
    safe_metrics,
    selected_metrics,
    utc,
)
from scripts.research_15m_ma_launch_l2_short_window_side_split import (
    empirical_percentile,
    strict_matched_control_metrics,
)
from yoyo.layers.l2_judgment.global_shape import (
    GLOBAL_CONTEXT_BARS,
    GLOBAL_SHAPE_FEATURE_COLUMNS,
    add_global_shape_indicators,
    extract_global_shape_features,
)


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-15m-ma-launch-l15-global-shape-l2-side-split-v1"
EXPERIMENT_DIR = ROOT / "experiments" / "active" / EXPERIMENT_ID
PREREG_PATH = EXPERIMENT_DIR / "preregistration.json"
RESULTS_DIR = EXPERIMENT_DIR / "results"
OUTPUT_DIR = ROOT / "analysis" / "output" / "ma_launch_l15_l2_pipeline_v1"
SIDES = ("long", "short")
LEARNING_SPLITS = ("train", "tune", "final_validation")
SEED = 42
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")

L2_REDUCED_FEATURES = (
    "l1_confidence",
    "ma_spread_pct",
    "spread_chg8",
    "spread_chg24",
    "dense_frac48",
    "close_vs_ema55",
    "close_vs_ema200",
    "slow_slope_12",
    "volume_ratio",
    "atr_pct",
    "atr_pct_ratio96",
    "pre_range48",
    "drawdown24",
    "ret_4",
    "ret_12",
    "ret_24",
    "ret_48",
)
L2_DETERMINISTIC_PARAMS = {
    "device_type": "cpu",
    "deterministic": True,
    "force_col_wise": True,
    "num_threads": 1,
    "data_random_seed": SEED,
    "feature_fraction_seed": SEED,
    "bagging_seed": SEED,
    "extra_seed": SEED,
}


class PipelineError(RuntimeError):
    """Fail-closed error for contract or artifact drift."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def repo_relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def repo_path(value: object) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else ROOT / path


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def runtime_versions() -> dict[str, str]:
    import lightgbm
    import sklearn

    return {
        "lightgbm": lightgbm.__version__,
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "scikit_learn": sklearn.__version__,
    }


def load_preregistration(path: Path = PREREG_PATH) -> dict[str, Any]:
    prereg = read_json(path)
    if prereg.get("experiment_id") != EXPERIMENT_ID:
        raise PipelineError("experiment id drift")
    if int(prereg["l15"]["context_bars"]) != GLOBAL_CONTEXT_BARS:
        raise PipelineError("L1.5 context contract drift")
    if tuple(prereg["l15"]["feature_columns"]) != GLOBAL_SHAPE_FEATURE_COLUMNS:
        raise PipelineError("L1.5 feature contract drift")
    if tuple(prereg["l2"]["feature_columns"]) != L2_REDUCED_FEATURES:
        raise PipelineError("L2 reduced feature contract drift")
    if prereg["safety"]["holdout_read"]:
        raise PipelineError("holdout read must remain false")
    for key in ("source_manifest", "candidate_dataset", "matched_controls"):
        spec = prereg["inputs"][key]
        path_value = repo_path(spec["path"])
        if not path_value.is_file() or sha256_file(path_value) != spec["sha256"]:
            raise PipelineError(f"immutable input mismatch: {key}")
    return prereg


def _split_for_l15(row: Mapping[str, Any], prereg: Mapping[str, Any]) -> str:
    if str(row["split"]) == "val":
        return "final_validation"
    decision = utc(row["window_end_time"])
    cutoff = utc(prereg["l15"]["train_tune_cutoff"])
    purge = pd.Timedelta(hours=float(prereg["l15"]["purge_hours_each_side"]))
    if decision < cutoff - purge:
        return "train"
    if decision >= cutoff + purge:
        return "tune"
    return "purge"


def _manifest_feature_row(
    row: Mapping[str, Any],
    enriched: pd.DataFrame,
    prereg: Mapping[str, Any],
) -> dict[str, Any]:
    positive = str(row["sample_kind"]) == "positive"
    side = str(row["direction"] if positive else row["paired_direction"]).lower()
    event_id = str(row["event_id"] if positive else row["negative_event_id"])
    core_end_i = int(row["source_core_end_i"] if positive else row["core_end_i"])
    decision_i = int(row["window_end_i"])
    confirmation = int(row["post_bars"])
    if decision_i - core_end_i != confirmation:
        raise PipelineError(f"confirmation geometry mismatch for {event_id}")
    features = extract_global_shape_features(
        enriched,
        decision_i=decision_i,
        core_end_i=core_end_i,
        side=side,
        confirmation_bars=confirmation,
    )
    return {
        "event_id": event_id,
        "paired_positive_event_id": str(row.get("paired_positive_event_id") or event_id),
        "sample_kind": "positive" if positive else "hard_negative",
        "label": int(positive),
        "side": side,
        "symbol": str(row["symbol"]),
        "source_path": str(row["source_path"]),
        "variant_id": str(row["variant_id"]),
        "decision_i": decision_i,
        "core_end_i": core_end_i,
        "decision_time": utc(row["window_end_time"]).isoformat(),
        "core_end_time": utc(row["core_end_time"]).isoformat(),
        "manifest_split": str(row["split"]),
        "split": _split_for_l15(row, prereg),
        **features,
    }


def build_l15_dataset(prereg: Mapping[str, Any]) -> dict[str, Any]:
    """Materialize event-grouped 128-bar L1.5 feature rows."""

    terminal = RESULTS_DIR / "l15_dataset_receipt.json"
    if terminal.exists():
        return read_json(terminal)
    manifest_path = repo_path(prereg["inputs"]["source_manifest"]["path"])
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with manifest_path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row["sample_kind"] == "negative" and row["negative_kind"] != "hard":
                continue
            grouped[str(row["source_path"])].append(row)
    rows: list[dict[str, Any]] = []
    for source_name in sorted(grouped):
        source_path = repo_path(source_name)
        enriched = add_global_shape_indicators(normalize_ohlcv(source_path))
        for row in grouped[source_name]:
            rows.append(_manifest_feature_row(row, enriched, prereg))
    data = pd.DataFrame(rows)
    if len(data) != int(prereg["l15"]["expected_variant_rows"]):
        raise PipelineError(f"unexpected L1.5 row count {len(data)}")
    if utc(data["decision_time"].max()) >= HOLDOUT_START:
        raise PipelineError("L1.5 dataset reaches holdout")
    event_split_counts = data.groupby("event_id")["split"].nunique()
    if int((event_split_counts > 1).sum()) != 0:
        raise PipelineError("one L1.5 event crosses learning splits")
    dataset_path = OUTPUT_DIR / "l15_dataset.csv"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    data.to_csv(dataset_path, index=False)
    counts = (
        data.groupby(["split", "side", "sample_kind"])
        .size()
        .rename("rows")
        .reset_index()
        .to_dict("records")
    )
    events = (
        data.groupby(["split", "side", "sample_kind"])["event_id"]
        .nunique()
        .rename("events")
        .reset_index()
        .to_dict("records")
    )
    receipt = {
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "builder_commit": git_head(),
        "dataset_path": repo_relative(dataset_path),
        "dataset_sha256": sha256_file(dataset_path),
        "rows": len(data),
        "events": int(data["event_id"].nunique()),
        "row_counts": counts,
        "event_counts": events,
        "feature_columns": list(GLOBAL_SHAPE_FEATURE_COLUMNS),
        "event_cross_split_failures": 0,
        "max_decision_time": str(data["decision_time"].max()),
        "holdout_consumed": False,
    }
    write_json(terminal, receipt)
    return receipt


def _event_representatives(data: pd.DataFrame, split: str) -> pd.DataFrame:
    subset = data[data["split"] == split].copy()
    subset["decision_time"] = pd.to_datetime(subset["decision_time"], utc=True)
    return (
        subset.sort_values(["decision_time", "confirmation_bars", "variant_id"])
        .groupby("event_id", as_index=False)
        .first()
    )


def choose_strict_threshold(
    labels: np.ndarray,
    scores: np.ndarray,
    *,
    max_false_positive_rate: float,
    minimum_true_positives: int,
) -> dict[str, float | int]:
    """Freeze the highest-recall threshold satisfying the tune strictness cap."""

    labels = np.asarray(labels, dtype=int)
    scores = np.asarray(scores, dtype=float)
    positives = int((labels == 1).sum())
    negatives = int((labels == 0).sum())
    choices: list[dict[str, float | int]] = []
    for threshold in sorted(np.unique(scores), reverse=True):
        pred = scores >= threshold
        tp = int(((labels == 1) & pred).sum())
        fp = int(((labels == 0) & pred).sum())
        fpr = fp / negatives if negatives else 0.0
        if fpr <= max_false_positive_rate and tp >= minimum_true_positives:
            choices.append(
                {
                    "threshold": float(threshold),
                    "tp": tp,
                    "fp": fp,
                    "recall": tp / positives if positives else 0.0,
                    "precision": tp / max(tp + fp, 1),
                    "false_positive_rate": fpr,
                }
            )
    if not choices:
        raise PipelineError("no L1.5 threshold satisfies preregistered tune constraints")
    choices.sort(
        key=lambda item: (
            float(item["recall"]),
            float(item["precision"]),
            float(item["threshold"]),
        ),
        reverse=True,
    )
    return choices[0]


def classification_metrics(labels: np.ndarray, scores: np.ndarray, threshold: float) -> dict[str, Any]:
    labels = np.asarray(labels, dtype=int)
    scores = np.asarray(scores, dtype=float)
    pred = scores >= float(threshold)
    negatives = max(1, int((labels == 0).sum()))
    return {
        "n": len(labels),
        "positive_rate": float(labels.mean()),
        "roc_auc": float(roc_auc_score(labels, scores)),
        "pr_auc": float(average_precision_score(labels, scores)),
        "accepted": int(pred.sum()),
        "precision": float(precision_score(labels, pred, zero_division=0)),
        "recall": float(recall_score(labels, pred, zero_division=0)),
        "false_positive_rate": float(((labels == 0) & pred).sum() / negatives),
        "permutation_p": float(_classification_permutation_p(labels, scores)),
    }


def _classification_permutation_p(labels: np.ndarray, scores: np.ndarray, n_perm: int = 10_000) -> float:
    observed = roc_auc_score(labels, scores)
    rng = np.random.default_rng(SEED)
    hits = 0
    for _ in range(n_perm):
        if roc_auc_score(rng.permutation(labels), scores) >= observed:
            hits += 1
    return (hits + 1) / (n_perm + 1)


def train_l15(prereg: Mapping[str, Any]) -> dict[str, Any]:
    """Train side-specific global-shape classifiers and freeze tune thresholds."""

    terminal = RESULTS_DIR / "l15_training_receipt.json"
    if terminal.exists():
        return read_json(terminal)
    dataset_receipt = read_json(RESULTS_DIR / "l15_dataset_receipt.json")
    dataset_path = repo_path(dataset_receipt["dataset_path"])
    if sha256_file(dataset_path) != dataset_receipt["dataset_sha256"]:
        raise PipelineError("L1.5 dataset hash mismatch")
    data = pd.read_csv(dataset_path)
    models_dir = OUTPUT_DIR / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    from yoyo.layers.l2_judgment.train import train_model

    arms: dict[str, Any] = {}
    scored_parts: list[pd.DataFrame] = []
    for side in SIDES:
        side_data = data[data["side"] == side].copy()
        train = side_data[side_data["split"] == "train"].copy()
        tune = _event_representatives(side_data, "tune")
        final = _event_representatives(side_data, "final_validation")
        if min(len(train), len(tune), len(final)) == 0:
            raise PipelineError(f"empty L1.5 split for {side}")
        model = train_model(
            train,
            tune,
            feature_columns=GLOBAL_SHAPE_FEATURE_COLUMNS,
            objective="binary",
            params_override=L2_DETERMINISTIC_PARAMS,
        )
        baseline = train_model(
            train,
            tune,
            feature_columns=["ma_spread_atr_end"],
            objective="binary",
            params_override=L2_DETERMINISTIC_PARAMS,
        )
        tune_score = model.predict(
            tune[list(GLOBAL_SHAPE_FEATURE_COLUMNS)], num_iteration=model.best_iteration
        )
        threshold = choose_strict_threshold(
            tune["label"].to_numpy(dtype=int),
            tune_score,
            max_false_positive_rate=float(prereg["l15"]["tune_max_false_positive_rate"]),
            minimum_true_positives=int(prereg["l15"]["tune_minimum_true_positives_per_side"]),
        )
        final_score = model.predict(
            final[list(GLOBAL_SHAPE_FEATURE_COLUMNS)], num_iteration=model.best_iteration
        )
        baseline_score = baseline.predict(
            final[["ma_spread_atr_end"]], num_iteration=baseline.best_iteration
        )
        final["l15_score"] = final_score
        final["l15_threshold"] = float(threshold["threshold"])
        final["l15_keep"] = final_score >= float(threshold["threshold"])
        scored_parts.append(final)
        model_path = models_dir / f"l15_global_shape_{side}.txt"
        baseline_path = models_dir / f"l15_spread_baseline_{side}.txt"
        model.save_model(str(model_path))
        baseline.save_model(str(baseline_path))
        metrics = classification_metrics(final["label"], final_score, float(threshold["threshold"]))
        baseline_metrics = classification_metrics(final["label"], baseline_score, 0.5)
        gate = {
            "final_false_positive_rate_lte": metrics["false_positive_rate"]
            <= float(prereg["l15"]["final_max_false_positive_rate"]),
            "final_recall_gte": metrics["recall"] >= float(prereg["l15"]["final_minimum_recall"]),
            "permutation_p_lt_0_01": metrics["permutation_p"] < 0.01,
            "beats_single_spread_auc": metrics["roc_auc"] > baseline_metrics["roc_auc"],
        }
        gate["passed"] = all(gate.values())
        arms[side] = {
            "splits": {"train_rows": len(train), "train_events": train["event_id"].nunique(), "tune_events": len(tune), "final_events": len(final)},
            "best_iteration": int(model.best_iteration),
            "threshold_selection": threshold,
            "final_metrics": metrics,
            "single_spread_baseline": baseline_metrics,
            "gate": gate,
            "model_path": repo_relative(model_path),
            "model_sha256": sha256_file(model_path),
            "baseline_path": repo_relative(baseline_path),
            "baseline_sha256": sha256_file(baseline_path),
        }
    scored = pd.concat(scored_parts, ignore_index=True)
    scored_path = OUTPUT_DIR / "l15_final_validation_scored.csv"
    scored.to_csv(scored_path, index=False)
    payload = {
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "builder_commit": git_head(),
        "runtime": runtime_versions(),
        "feature_columns": list(GLOBAL_SHAPE_FEATURE_COLUMNS),
        "arms": arms,
        "gate_passed": all(arms[side]["gate"]["passed"] for side in SIDES),
        "scored_path": repo_relative(scored_path),
        "scored_sha256": sha256_file(scored_path),
        "holdout_consumed": False,
        "production_eligible": False,
    }
    write_json(terminal, payload)
    return payload


def apply_l15_to_candidates(prereg: Mapping[str, Any]) -> dict[str, Any]:
    """Score every frozen L1 episode at its original decision time."""

    terminal = RESULTS_DIR / "candidate_l15_receipt.json"
    if terminal.exists():
        return read_json(terminal)
    import lightgbm as lgb

    source_spec = prereg["inputs"]["candidate_dataset"]
    data = pd.read_csv(repo_path(source_spec["path"]))
    data["dependency_representative"] = data["dependency_representative"].astype(str).str.lower() == "true"
    feature_rows: list[dict[str, float]] = []
    for symbol, indices in data.groupby("symbol", sort=True).groups.items():
        snapshot_path = repo_path(prereg["inputs"]["snapshot_dir"]) / f"{symbol}.csv"
        if not snapshot_path.is_file():
            raise PipelineError(f"missing snapshot for {symbol}")
        enriched = add_global_shape_indicators(normalize_ohlcv(snapshot_path))
        for index in indices:
            row = data.loc[index]
            feature_rows.append(
                {
                    "_index": int(index),
                    **extract_global_shape_features(
                        enriched,
                        decision_i=int(row["feature_bar_i"]),
                        core_end_i=int(row["core_end_i"]),
                        side=str(row["side"]),
                        confirmation_bars=int(row["confirmation_bars"]),
                    ),
                }
            )
    feature_frame = pd.DataFrame(feature_rows).set_index("_index").sort_index()
    if not feature_frame.index.equals(data.index):
        raise PipelineError("candidate L1.5 feature row alignment failed")
    l15_receipt = read_json(RESULTS_DIR / "l15_training_receipt.json")
    data["l15_score"] = np.nan
    data["l15_threshold"] = np.nan
    for side in SIDES:
        mask = data["side"] == side
        arm = l15_receipt["arms"][side]
        model_path = repo_path(arm["model_path"])
        if sha256_file(model_path) != arm["model_sha256"]:
            raise PipelineError(f"L1.5 model hash mismatch: {side}")
        model = lgb.Booster(model_file=str(model_path))
        data.loc[mask, "l15_score"] = model.predict(feature_frame.loc[mask, list(GLOBAL_SHAPE_FEATURE_COLUMNS)])
        data.loc[mask, "l15_threshold"] = float(arm["threshold_selection"]["threshold"])
    data["l15_keep"] = data["l15_score"] >= data["l15_threshold"]
    for column in GLOBAL_SHAPE_FEATURE_COLUMNS:
        data[f"l15_{column}"] = feature_frame[column].to_numpy()
    if utc(data["exposure_end_exclusive"].max()) > HOLDOUT_START:
        raise PipelineError("candidate label exposure reaches holdout")
    output_path = OUTPUT_DIR / "candidate_l15_scored.csv"
    data.to_csv(output_path, index=False)
    counts = (
        data.groupby(["split", "side", "dependency_representative", "l15_keep"])
        .size()
        .rename("rows")
        .reset_index()
        .to_dict("records")
    )
    payload = {
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "path": repo_relative(output_path),
        "sha256": sha256_file(output_path),
        "rows": len(data),
        "counts": counts,
        "max_exposure_end_exclusive": str(data["exposure_end_exclusive"].max()),
        "holdout_consumed": False,
    }
    write_json(terminal, payload)
    return payload


def _learning_rows(data: pd.DataFrame, split: str) -> pd.DataFrame:
    return data[(data["split"] == split) & data["dependency_representative"]].copy()


def _train_l2_side(
    data: pd.DataFrame,
    *,
    side: str,
    arm_name: str,
    feature_columns: Sequence[str],
) -> tuple[dict[str, Any], pd.DataFrame, np.ndarray]:
    from yoyo.layers.l2_judgment.train import train_model

    subset = data[data["side"] == side].copy()
    train = _learning_rows(subset, "train")
    tune = _learning_rows(subset, "tune")
    final_events = subset[subset["split"] == "final_validation"].copy()
    final = final_events[final_events["dependency_representative"]].copy()
    minimum = 20
    if min(len(train), len(tune), len(final)) < minimum:
        raise PipelineError(
            f"{arm_name}/{side} has too few independent rows: "
            f"train={len(train)} tune={len(tune)} final={len(final)}"
        )
    model = train_model(
        train,
        tune,
        feature_columns=feature_columns,
        objective="regression",
        params_override=L2_DETERMINISTIC_PARAMS,
    )
    tune_score = model.predict(tune[list(feature_columns)], num_iteration=model.best_iteration)
    threshold = float(np.quantile(tune_score, 0.9))
    final_score = model.predict(final_events[list(feature_columns)], num_iteration=model.best_iteration)
    final_events[f"{arm_name}_score"] = final_score
    final_events[f"{arm_name}_percentile"] = empirical_percentile(tune_score, final_score)
    final_events[f"{arm_name}_threshold"] = threshold
    final_events[f"{arm_name}_keep"] = final_score >= threshold
    model_path = OUTPUT_DIR / "models" / f"l2_{arm_name}_{side}.txt"
    model.save_model(str(model_path))
    return (
        {
            "side": side,
            "splits": {"train": len(train), "tune": len(tune), "final_validation": len(final)},
            "best_iteration": int(model.best_iteration),
            "tune_q90_threshold": threshold,
            "model_path": repo_relative(model_path),
            "model_sha256": sha256_file(model_path),
        },
        final_events,
        tune_score,
    )


def _economic_arm_metrics(
    scored_by_side: Mapping[str, pd.DataFrame],
    controls: pd.DataFrame,
    prereg: Mapping[str, Any],
    *,
    arm_name: str,
) -> dict[str, Any]:
    combined = pd.concat([scored_by_side[side] for side in SIDES], ignore_index=True)
    final = combined[combined["dependency_representative"]].copy()
    score = final[f"{arm_name}_percentile"].to_numpy(dtype=float)
    keep = final[f"{arm_name}_keep"].to_numpy(dtype=bool)
    returns = final["realized_ret"].to_numpy(dtype=float)
    cost = float(prereg["outcome"]["round_trip_cost_fraction"])
    selected_ids = set(final.loc[keep, "episode_id"].astype(str))
    by_side: dict[str, Any] = {}
    for side in SIDES:
        subset = final[final["side"] == side]
        side_score = subset[f"{arm_name}_score"].to_numpy(dtype=float)
        side_keep = subset[f"{arm_name}_keep"].to_numpy(dtype=bool)
        by_side[side] = {
            "rank": safe_metrics(subset["label"].to_numpy(dtype=int), side_score, subset["realized_ret"].to_numpy(dtype=float), cost),
            "frozen_q90": selected_metrics(subset, side_keep, cost),
            "permutation_p": outcome_permutation_pvalue(side_score, subset["realized_ret"].to_numpy(dtype=float)),
        }
    return {
        "rank": safe_metrics(final["label"].to_numpy(dtype=int), score, returns, cost),
        "frozen_q90": selected_metrics(final, keep, cost),
        "permutation_p": outcome_permutation_pvalue(score, returns),
        "matched_control": strict_matched_control_metrics(
            final,
            controls,
            selected_ids,
            required_assignments=int(prereg["matched_control"]["deterministic_assignments"]),
        ),
        "by_side": by_side,
        "scored": combined,
    }


def train_l2_factorial(prereg: Mapping[str, Any]) -> dict[str, Any]:
    """Compare L1, L1.5, L2 and L1.5+L2 on the same frozen candidates."""

    terminal = RESULTS_DIR / "l2_training_receipt.json"
    if terminal.exists():
        return read_json(terminal)
    candidate_receipt = read_json(RESULTS_DIR / "candidate_l15_receipt.json")
    candidate_path = repo_path(candidate_receipt["path"])
    if sha256_file(candidate_path) != candidate_receipt["sha256"]:
        raise PipelineError("candidate L1.5 ledger hash mismatch")
    data = pd.read_csv(candidate_path)
    data["dependency_representative"] = data["dependency_representative"].astype(str).str.lower() == "true"
    data["l15_keep"] = data["l15_keep"].astype(str).str.lower() == "true"
    controls = pd.read_csv(repo_path(prereg["inputs"]["matched_controls"]["path"]))
    arms: dict[str, Any] = {}
    final_outputs: dict[str, pd.DataFrame] = {}
    for arm_name, arm_data in (
        ("l2_only", data),
        ("l15_l2", data[data["l15_keep"]].copy()),
    ):
        side_receipts: dict[str, Any] = {}
        scored_by_side: dict[str, pd.DataFrame] = {}
        for side in SIDES:
            side_receipts[side], scored_by_side[side], _ = _train_l2_side(
                arm_data,
                side=side,
                arm_name=arm_name,
                feature_columns=L2_REDUCED_FEATURES,
            )
        metrics = _economic_arm_metrics(scored_by_side, controls, prereg, arm_name=arm_name)
        scored = metrics.pop("scored")
        final_outputs[arm_name] = scored
        gate = {
            "top_decile_net_positive": metrics["rank"]["top_decile"]["net_mean"] > 0,
            "frozen_q90_net_positive": metrics["frozen_q90"]["net_mean"] is not None and metrics["frozen_q90"]["net_mean"] > 0,
            "minimum_30_selected": metrics["frozen_q90"]["n"] >= 30,
            "outcome_permutation_p_lt_0_01": metrics["permutation_p"] < 0.01,
            "matched_controls_complete": metrics["matched_control"]["complete_assignment_coverage"],
            "beats_matched_controls": metrics["matched_control"]["all_assignments_positive"],
            "neither_side_q90_negative": all(metrics["by_side"][side]["frozen_q90"]["net_mean"] is not None and metrics["by_side"][side]["frozen_q90"]["net_mean"] >= 0 for side in SIDES),
        }
        gate["passed"] = all(gate.values())
        arms[arm_name] = {"models": side_receipts, "metrics": metrics, "gate": gate}

    final_all = data[(data["split"] == "final_validation") & data["dependency_representative"]].copy()
    cost = float(prereg["outcome"]["round_trip_cost_fraction"])
    l1_rank = safe_metrics(
        final_all["label"].to_numpy(dtype=int),
        final_all["l1_confidence"].to_numpy(dtype=float),
        final_all["realized_ret"].to_numpy(dtype=float),
        cost,
    )
    l15_keep = final_all["l15_keep"].to_numpy(dtype=bool)
    l15_metrics = selected_metrics(final_all, l15_keep, cost)
    l15_selected_ids = set(final_all.loc[l15_keep, "episode_id"].astype(str))
    l15_controls = strict_matched_control_metrics(
        final_all,
        controls,
        l15_selected_ids,
        required_assignments=int(prereg["matched_control"]["deterministic_assignments"]),
    )
    pipeline_scored = final_outputs["l15_l2"]
    final_path = OUTPUT_DIR / "pipeline_final_validation_scored.csv"
    pipeline_scored.to_csv(final_path, index=False)
    payload = {
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "builder_commit": git_head(),
        "runtime": runtime_versions(),
        "feature_columns": list(L2_REDUCED_FEATURES),
        "l1_only": {"rank": l1_rank, "final_independent_events": len(final_all)},
        "l15_only": {"frozen_filter": l15_metrics, "matched_control": l15_controls},
        "arms": arms,
        "pipeline_scored_path": repo_relative(final_path),
        "pipeline_scored_sha256": sha256_file(final_path),
        "validation_status": "retrospective_development_only_final_period_previously_consumed",
        "fresh_validation_required": True,
        "holdout_consumed": False,
        "promoted": False,
        "deployed": False,
        "production_eligible": False,
    }
    write_json(terminal, payload)
    return payload


def render_candidate_chart(row: Mapping[str, Any], frame: pd.DataFrame) -> np.ndarray:
    """Render 128 decision-time bars and reproject the preserved raw L1 box."""

    from yoyo.layers.l1_detection.data import add_mas
    from yoyo.layers.l1_detection.render import render_chart

    enriched = add_mas(frame)
    signal_i = int(row["feature_bar_i"])
    context_start = signal_i - GLOBAL_CONTEXT_BARS + 1
    context = enriched.iloc[context_start : signal_i + 1]
    chart, context_tf = render_chart(context, width=1920, height=1113, out_path=None)
    input_window = enriched.iloc[int(row["window_start_i"]) : signal_i + 1]
    exact_input, input_tf = render_chart(input_window, out_path=None)
    if pixel_sha256(exact_input) != str(row["input_pixel_sha256"]):
        raise PipelineError(f"L1 input pixel parity failed for {row['episode_id']}")
    raw_x0, raw_y0, raw_x1, raw_y1 = normalized_box_corners(row, input_tf.width, input_tf.height)

    def inverse_x(pixel: int) -> float:
        return (pixel - input_tf.left) / input_tf.plot_w * (input_tf.n_bars - 1)

    def inverse_y(pixel: int) -> float:
        return input_tf.price_max - (pixel - input_tf.top) / input_tf.plot_h * (input_tf.price_max - input_tf.price_min)

    global_x0 = int(row["window_start_i"]) + inverse_x(raw_x0)
    global_x1 = int(row["window_start_i"]) + inverse_x(raw_x1)
    x0 = int(round(context_tf.left + (global_x0 - context_start) / (context_tf.n_bars - 1) * context_tf.plot_w))
    x1 = int(round(context_tf.left + (global_x1 - context_start) / (context_tf.n_bars - 1) * context_tf.plot_w))
    y0, y1 = context_tf.y_at(inverse_y(raw_y0)), context_tf.y_at(inverse_y(raw_y1))
    canvas = np.full((1250, 1920, 3), 255, dtype=np.uint8)
    canvas[137:, :] = chart
    color = CLASS_COLORS[int(row["class_id"])]
    cv2.rectangle(canvas, (x0, y0 + 137), (x1, y1 + 137), color, 5, cv2.LINE_AA)
    l15_state = "PASS" if bool(row["l15_keep"]) else "REJECT"
    pipeline_state = "KEEP" if bool(row.get("l15_l2_keep", False)) else "DROP"
    title = f"{row['symbol']} | {str(row['side']).upper()} | L1.5 {l15_state} | PIPELINE {pipeline_state}"
    detail = (
        f"available {utc(row['available_at']):%Y-%m-%d %H:%M} UTC | "
        f"L1={float(row['l1_confidence']):.3f} L1.5={float(row['l15_score']):.3f} | "
        "128 closed bars; no future outcome shown"
    )
    cv2.putText(canvas, title, (28, 48), cv2.FONT_HERSHEY_SIMPLEX, 0.86, (20, 20, 20), 2, cv2.LINE_AA)
    cv2.putText(canvas, detail, (28, 94), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (60, 60, 60), 1, cv2.LINE_AA)
    return canvas


def render_review(prereg: Mapping[str, Any]) -> dict[str, Any]:
    terminal = RESULTS_DIR / "render_receipt.json"
    if terminal.exists():
        return read_json(terminal)
    candidate = pd.read_csv(repo_path(read_json(RESULTS_DIR / "candidate_l15_receipt.json")["path"]))
    candidate["dependency_representative"] = candidate["dependency_representative"].astype(str).str.lower() == "true"
    candidate["l15_keep"] = candidate["l15_keep"].astype(str).str.lower() == "true"
    pipeline = pd.read_csv(repo_path(read_json(RESULTS_DIR / "l2_training_receipt.json")["pipeline_scored_path"]))
    pipeline["l15_l2_keep"] = pipeline["l15_l2_keep"].astype(str).str.lower() == "true"
    final = candidate[(candidate["split"] == "final_validation") & candidate["dependency_representative"]].copy()
    final = final.merge(
        pipeline[["episode_id", "l15_l2_score", "l15_l2_keep"]],
        on="episode_id",
        how="left",
    )
    final["l15_l2_keep"] = final["l15_l2_keep"].fillna(False)
    selected = final[final["l15_l2_keep"]].sort_values("l15_l2_score", ascending=False).head(20)
    rejected = final[~final["l15_keep"]].sort_values("l1_confidence", ascending=False).head(20)
    review = pd.concat([selected, rejected], ignore_index=True).drop_duplicates("episode_id").head(40)
    review_dir = OUTPUT_DIR / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    snapshots: dict[str, pd.DataFrame] = {}
    for order, row in review.iterrows():
        symbol = str(row["symbol"])
        if symbol not in snapshots:
            snapshots[symbol] = normalize_ohlcv(repo_path(prereg["inputs"]["snapshot_dir"]) / f"{symbol}.csv")
        image = render_candidate_chart(row, snapshots[symbol])
        state = "keep" if bool(row["l15_l2_keep"]) else "reject"
        name = f"{order + 1:02d}_{state}_{symbol}_{str(row['side'])}_{row['episode_id']}.png"
        path = review_dir / name
        if not cv2.imwrite(str(path), image):
            raise PipelineError(f"failed to write {path}")
        records.append({"order": order + 1, "episode_id": row["episode_id"], "state": state, "path": repo_relative(path), "sha256": sha256_file(path)})
    thumbs = []
    for record in records[:24]:
        image = cv2.imread(str(repo_path(record["path"])))
        thumbs.append(cv2.resize(image, (480, 312), interpolation=cv2.INTER_AREA))
    if thumbs:
        cols = 4
        rows = (len(thumbs) + cols - 1) // cols
        overview = np.full((rows * 312, cols * 480, 3), 245, dtype=np.uint8)
        for index, thumb in enumerate(thumbs):
            y, x = divmod(index, cols)
            overview[y * 312 : (y + 1) * 312, x * 480 : (x + 1) * 480] = thumb
        overview_path = OUTPUT_DIR / "pipeline_review_overview.png"
        cv2.imwrite(str(overview_path), overview)
    else:
        raise PipelineError("no review charts selected")
    manifest_path = OUTPUT_DIR / "review_manifest.json"
    write_json(manifest_path, {"records": records})
    payload = {
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "charts": len(records),
        "manifest_path": repo_relative(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "overview_path": repo_relative(overview_path),
        "overview_sha256": sha256_file(overview_path),
        "pixel_parity_failures": 0,
        "holdout_consumed": False,
    }
    write_json(terminal, payload)
    return payload


def verify_outputs(prereg: Mapping[str, Any]) -> dict[str, Any]:
    terminal = RESULTS_DIR / "verify_receipt.json"
    checks: dict[str, bool] = {}
    for name in (
        "l15_dataset_receipt.json",
        "l15_training_receipt.json",
        "candidate_l15_receipt.json",
        "l2_training_receipt.json",
        "render_receipt.json",
    ):
        checks[f"exists_{name}"] = (RESULTS_DIR / name).is_file()
    l15 = read_json(RESULTS_DIR / "l15_training_receipt.json")
    l2 = read_json(RESULTS_DIR / "l2_training_receipt.json")
    render = read_json(RESULTS_DIR / "render_receipt.json")
    checks["l15_side_models_present"] = all(repo_path(l15["arms"][side]["model_path"]).is_file() for side in SIDES)
    checks["l2_four_side_models_present"] = all(
        repo_path(l2["arms"][arm]["models"][side]["model_path"]).is_file()
        for arm in ("l2_only", "l15_l2")
        for side in SIDES
    )
    checks["no_holdout_consumption"] = not any(bool(receipt.get("holdout_consumed")) for receipt in (l15, l2, render))
    checks["fresh_validation_required"] = bool(l2["fresh_validation_required"])
    checks["no_production_eligibility"] = not bool(l2["production_eligible"])
    checks["render_pixel_parity"] = int(render["pixel_parity_failures"]) == 0
    checks["feature_contracts_match"] = tuple(l15["feature_columns"]) == GLOBAL_SHAPE_FEATURE_COLUMNS and tuple(l2["feature_columns"]) == L2_REDUCED_FEATURES
    payload = {
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "passed": all(checks.values()),
        "holdout_consumed": False,
    }
    write_json(terminal, payload)
    if not payload["passed"]:
        raise PipelineError(f"verification failed: {[key for key, value in checks.items() if not value]}")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-l15-dataset", action="store_true")
    parser.add_argument("--train-l15", action="store_true")
    parser.add_argument("--apply-l15", action="store_true")
    parser.add_argument("--train-l2", action="store_true")
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--all", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    prereg = load_preregistration()
    if args.all or args.build_l15_dataset:
        build_l15_dataset(prereg)
    if args.all or args.train_l15:
        train_l15(prereg)
    if args.all or args.apply_l15:
        apply_l15_to_candidates(prereg)
    if args.all or args.train_l2:
        train_l2_factorial(prereg)
    if args.all or args.render:
        render_review(prereg)
    if args.all or args.verify:
        verify_outputs(prereg)


if __name__ == "__main__":
    main()

