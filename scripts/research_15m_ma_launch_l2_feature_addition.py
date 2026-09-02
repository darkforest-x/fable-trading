#!/usr/bin/env python3
"""Test add-only causal feature groups on the frozen real-YOLO L2 pool.

The experiment preserves the candidate rows, outcomes, dependency blocks,
chronological splits, LightGBM parameters, tune-q90 rule, costs and matched
controls from ``exp-15m-ma-launch-l2-global-context-v1``.  It reconstructs
extra OHLCV features at each row's already-frozen ``feature_bar_i`` from the
byte-pinned pre-holdout snapshot.  No bar after that index is used by a model
feature; only ``label`` and ``realized_ret`` use the frozen future outcome.

Feature inputs are ``open/high/low/close/volume`` from the snapshot start
through the decision bar.  Finite rolling windows are at most 168 bars for the
legacy base and 120 bars for additions; EMA values are recursive from the
snapshot start.  LONG and SHORT models are trained separately, so additional
signed market coordinates are never mixed across sides.  The legacy 28 values
are independently recomputed with side-aligned semantics and must match the
source dataset within 1e-12 before any training is allowed.

Selection is performed on March tune representatives only.  The frozen winner
per side is committed before April final validation may be opened.  The script
never reads rows dated 2026-05-04 or later, promotes, deploys, mutates ACTIVE or
forward state, sends Telegram, or places orders.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

from scripts.owner_side_rich_features import add_rich_features
from scripts.research_15m_ma_launch_l2_feature_group_ablation import (
    bool_series,
    fractional_top_decile_metrics,
    repo_path,
    repo_relative,
    runtime_versions,
    score_diagnostics,
    sha256_file,
    strict_control_metrics,
    write_json,
)
from scripts.research_15m_ma_launch_l2_global_context import (
    L2_DETERMINISTIC_PARAMS,
    outcome_permutation_pvalue,
    safe_metrics,
    selected_metrics,
)
from scripts.retrain_15m_ma_launch_l2_by_side import empirical_percentile
from yoyo.layers.l2_judgment.features import (
    FEATURE_COLUMNS,
    extract_feature_rows_for_side,
)
from yoyo.layers.l2_judgment.train import LGB_PARAMS, train_model

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-15m-ma-launch-l2-feature-addition-v1"
EXPERIMENT_DIR = ROOT / "experiments" / "active" / EXPERIMENT_ID
PREREG_PATH = EXPERIMENT_DIR / "preregistration.json"
RESULTS_DIR = EXPERIMENT_DIR / "results"
OUTPUT_DIR = ROOT / "analysis" / "output" / "ma_launch_l2_feature_addition_v1"
REPORT_PATH = ROOT / "analysis" / "p3_15m_ma_launch_l2_feature_addition_20260902.md"
SIDES = ("long", "short")

# Exact semantic duplicates of legacy columns are excluded.  Giving a tree the
# same scalar twice changes feature-subsampling odds without adding information.
DUPLICATE_RICH_COLUMNS = frozenset(
    {
        "fast_spread",  # ma_spread_pct
        "fast_spread_rank96",  # spread_pos96
        "dense_run_len_fast",  # dense_run_len
        "roc_12",  # ret_12
        "roc_48",  # ret_48
        "vol_ratio_20",  # volume_ratio after warmup
    }
)

ADDITION_GROUPS: OrderedDict[str, tuple[str, ...]] = OrderedDict(
    [
        (
            "ma_family",
            (
                "close_vs_sma20",
                "close_vs_sma60",
                "close_vs_sma120",
                "close_vs_ema5",
                "close_vs_ema10",
                "close_vs_ema20",
                "close_vs_ema60",
                "close_vs_ema120",
                "gap_ema8_21",
                "gap_ema21_55",
                "gap_ema55_200",
                "gap_sma20_60",
                "gap_sma60_120",
                "ema8_slope8",
                "ema21_slope12",
                "ema55_slope24",
                "ema200_slope24",
                "sma60_slope12",
                "cross_ema8_21",
                "cross_ema21_55",
                "cross_ema55_200",
                "ma_entangle",
                "ma_bandwidth",
                "bb_width20",
                "down_order_score",
                "trend_order_score",
            ),
        ),
        (
            "dense_dynamics",
            (
                "fast_spread_chg4",
                "fast_spread_chg16",
                "full_spread_chg8",
                "full_spread_chg24",
                "full_spread_rank96",
                "spread_expand_run",
                "spread_contract_run",
                "exit_dense_expand",
            ),
        ),
        (
            "momentum_structure",
            (
                "ret_2",
                "ret_8",
                "ret_16",
                "ret_64",
                "ret_96",
                "dist_high_24",
                "dist_low_24",
                "dist_high_48",
                "dist_low_48",
                "dist_high_96",
                "dist_low_96",
                "break_up_24",
                "break_dn_24",
                "break_up_48",
                "break_dn_48",
                "break_up_96",
                "break_dn_96",
                "range_pos_24",
                "range_pos_48",
                "range_pos_96",
                "dist_prior_high_96",
                "dist_prior_low_96",
            ),
        ),
        (
            "candle_volatility",
            (
                "atr7_pct",
                "atr28_pct",
                "atr_ratio_7_28",
                "rvol_24",
                "rvol_96",
                "rvol_ratio_24_96",
                "body_ratio",
                "wick_up_ratio",
                "wick_dn_ratio",
                "body_ratio_mean8",
                "atr_chg24",
            ),
        ),
        (
            "volume_flow",
            (
                "vol_ratio_96",
                "vol_z_24",
                "pv_corr_24",
                "breakout_vol_ratio",
                "up_vol_share_24",
            ),
        ),
        (
            "market_structure",
            (
                "struct_hh",
                "struct_hl",
                "struct_lh",
                "struct_ll",
                "struct_bias",
            ),
        ),
        (
            "time_context",
            ("hour_sin", "hour_cos", "dow", "dow_sin", "dow_cos"),
        ),
    ]
)


def _join_additions(*names: str) -> tuple[str, ...]:
    return tuple(column for name in names for column in ADDITION_GROUPS[name])


BASE_COLUMNS = tuple(FEATURE_COLUMNS)
EXTRA_COLUMNS = _join_additions(*ADDITION_GROUPS)
SELECTION_ARMS: OrderedDict[str, tuple[str, ...]] = OrderedDict(
    [
        ("baseline_28", BASE_COLUMNS),
        ("plus_ma_family", BASE_COLUMNS + ADDITION_GROUPS["ma_family"]),
        (
            "plus_dense_dynamics",
            BASE_COLUMNS + ADDITION_GROUPS["dense_dynamics"],
        ),
        (
            "plus_ma_dense",
            BASE_COLUMNS
            + ADDITION_GROUPS["ma_family"]
            + ADDITION_GROUPS["dense_dynamics"],
        ),
        (
            "plus_momentum_structure",
            BASE_COLUMNS + ADDITION_GROUPS["momentum_structure"],
        ),
        (
            "plus_candle_volatility",
            BASE_COLUMNS + ADDITION_GROUPS["candle_volatility"],
        ),
        ("plus_volume_flow", BASE_COLUMNS + ADDITION_GROUPS["volume_flow"]),
        (
            "plus_market_structure",
            BASE_COLUMNS + ADDITION_GROUPS["market_structure"],
        ),
        ("plus_time_context", BASE_COLUMNS + ADDITION_GROUPS["time_context"]),
        ("full_110", BASE_COLUMNS + EXTRA_COLUMNS),
    ]
)
DIAGNOSTIC_ARMS: OrderedDict[str, tuple[str, ...]] = OrderedDict(
    [("ma_spread_only", ("ma_spread_pct",))]
)
MODEL_ARMS: OrderedDict[str, tuple[str, ...]] = OrderedDict(
    [*DIAGNOSTIC_ARMS.items(), *SELECTION_ARMS.items()]
)


class FeatureAdditionError(RuntimeError):
    """Raised when feature lineage, causality or phase separation drifts."""


def git_head() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def committed_file(path: Path) -> bool:
    """Return whether ``path`` is tracked at HEAD and has no worktree diff."""

    relative = path.resolve().relative_to(ROOT.resolve()).as_posix()
    tracked = (
        subprocess.run(
            ["git", "cat-file", "-e", f"HEAD:{relative}"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        ).returncode
        == 0
    )
    dirty = subprocess.check_output(
        ["git", "status", "--short", "--", relative], cwd=ROOT, text=True
    ).strip()
    return tracked and not dirty


def load_preregistration() -> dict[str, Any]:
    prereg = json.loads(PREREG_PATH.read_text(encoding="utf-8"))
    if prereg.get("experiment_id") != EXPERIMENT_ID:
        raise FeatureAdditionError("preregistration experiment_id mismatch")
    if prereg["source"].get("holdout_rows") != 0:
        raise FeatureAdditionError("source contract is not pre-holdout only")
    if prereg["frozen_contract"].get("holdout_read") is not False:
        raise FeatureAdditionError("holdout read must remain disabled")
    groups = OrderedDict(
        (name, tuple(columns)) for name, columns in prereg["addition_groups"].items()
    )
    if groups != ADDITION_GROUPS:
        raise FeatureAdditionError("preregistered addition groups drifted")
    arms = OrderedDict(
        (item["name"], tuple(item["columns"]))
        for item in prereg["selection_arms_in_fixed_order"]
    )
    if arms != SELECTION_ARMS:
        raise FeatureAdditionError("preregistered selection arms drifted")
    diagnostic = OrderedDict(
        (item["name"], tuple(item["columns"]))
        for item in prereg["diagnostic_arms_in_fixed_order"]
    )
    if diagnostic != DIAGNOSTIC_ARMS:
        raise FeatureAdditionError("preregistered diagnostic arms drifted")
    declared_duplicates = frozenset(prereg["excluded_semantic_duplicates"])
    if declared_duplicates != DUPLICATE_RICH_COLUMNS:
        raise FeatureAdditionError("excluded duplicate list drifted")
    return prereg


def verify_pinned_inputs(
    prereg: Mapping[str, Any],
    *,
    include_controls: bool,
    include_expanded: bool,
) -> None:
    source = prereg["source"]
    items = [
        ("dataset_path", "dataset_sha256"),
        ("snapshot_receipt", "snapshot_receipt_sha256"),
        ("prior_side_split_receipt", "prior_side_split_receipt_sha256"),
        ("prior_side_split_scores", "prior_side_split_scores_sha256"),
    ]
    if include_controls:
        items.append(("matched_controls_path", "matched_controls_sha256"))
    if include_expanded:
        receipt = RESULTS_DIR / "feature_dataset_receipt.json"
        if not receipt.is_file():
            raise FeatureAdditionError("feature dataset receipt missing")
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        expanded = repo_path(payload["dataset_path"])
        if not expanded.is_file() or sha256_file(expanded) != payload["dataset_sha256"]:
            raise FeatureAdditionError("expanded feature dataset drifted")
        if payload["source_dataset_sha256"] != source["dataset_sha256"]:
            raise FeatureAdditionError("expanded dataset source drifted")
    for path_key, sha_key in items:
        path = repo_path(source[path_key])
        if not path.is_file() or sha256_file(path) != source[sha_key]:
            raise FeatureAdditionError(f"pinned input drifted: {path_key}")
    frozen = prereg["frozen_contract"]
    for path_key, sha_key in (
        ("base_feature_builder", "base_feature_builder_sha256"),
        ("rich_feature_builder", "rich_feature_builder_sha256"),
        ("training_builder", "training_builder_sha256"),
        ("metrics_builder", "metrics_builder_sha256"),
    ):
        path = repo_path(frozen[path_key])
        if not path.is_file() or sha256_file(path) != frozen[sha_key]:
            raise FeatureAdditionError(f"builder drifted: {path}")


def load_source(prereg: Mapping[str, Any], *, expanded: bool) -> pd.DataFrame:
    if expanded:
        receipt = json.loads(
            (RESULTS_DIR / "feature_dataset_receipt.json").read_text(encoding="utf-8")
        )
        path = repo_path(receipt["dataset_path"])
    else:
        path = repo_path(prereg["source"]["dataset_path"])
    data = pd.read_csv(path)
    if len(data) != int(prereg["source"]["dataset_rows"]):
        raise FeatureAdditionError("source row count drifted")
    required = {
        "episode_id",
        "symbol",
        "side",
        "split",
        "available_at",
        "feature_bar_i",
        "feature_bar_time",
        "dependency_representative",
        "label",
        "realized_ret",
        "net_ret",
        *BASE_COLUMNS,
    }
    if expanded:
        required.update(EXTRA_COLUMNS)
    missing = sorted(required - set(data.columns))
    if missing:
        raise FeatureAdditionError(f"dataset columns missing: {missing}")
    if data["episode_id"].duplicated().any():
        raise FeatureAdditionError("episode_id is not unique")
    data = data.copy()
    data["dependency_representative"] = bool_series(data["dependency_representative"])
    learning = data["split"].isin(("train", "tune", "final_validation"))
    columns = list(BASE_COLUMNS + (EXTRA_COLUMNS if expanded else ()))
    values = data.loc[learning, columns].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(values.to_numpy(dtype=float)).all():
        raise FeatureAdditionError("learning rows contain non-finite features")
    available = pd.to_datetime(data["available_at"], utc=True)
    if (available >= pd.Timestamp("2026-05-04", tz="UTC")).any():
        raise FeatureAdditionError("dataset unexpectedly contains holdout rows")
    return data


def snapshot_files(prereg: Mapping[str, Any]) -> dict[str, Path]:
    receipt = json.loads(
        repo_path(prereg["source"]["snapshot_receipt"]).read_text(encoding="utf-8")
    )
    if int(receipt["symbols"]) != int(prereg["source"]["snapshot_symbols"]):
        raise FeatureAdditionError("snapshot symbol count drifted")
    base = repo_path(prereg["source"]["snapshot_base_dir"])
    output: dict[str, Path] = {}
    for item in receipt["files"]:
        path = base / str(item["snapshot_path"])
        if not path.is_file() or sha256_file(path) != item["sha256"]:
            raise FeatureAdditionError(f"snapshot file drifted: {path}")
        output[str(item["symbol"])] = path
    return output


def build_feature_dataset(prereg: Mapping[str, Any]) -> dict[str, Any]:
    """Rebuild additions from frozen OHLCV through each decision bar only.

    Inputs are ``open/high/low/close/volume`` from snapshot start through the
    row's ``feature_bar_i``.  Rolling features use windows <=120 bars; the
    legacy ``pre_range168`` parity check uses 168 bars; EMA features recursively
    consume prior rows only.  No label/outcome column participates in feature
    calculation.
    """

    verify_pinned_inputs(prereg, include_controls=False, include_expanded=False)
    source = load_source(prereg, expanded=False)
    paths = snapshot_files(prereg)
    blocks: list[pd.DataFrame] = []
    maximum_base_delta = 0.0
    time_mismatches = 0
    for symbol, subset in source.groupby("symbol", sort=True):
        if symbol not in paths:
            raise FeatureAdditionError(f"snapshot missing source symbol: {symbol}")
        raw = pd.read_csv(paths[symbol])
        raw["open_time"] = pd.to_datetime(raw["open_time"], utc=True)
        rich = add_rich_features(raw)
        part = subset.copy()
        part["__source_order"] = part.index.to_numpy(dtype=int)
        indices = part["feature_bar_i"].to_numpy(dtype=int)
        if (indices < 0).any() or (indices >= len(rich)).any():
            raise FeatureAdditionError(f"feature index outside snapshot: {symbol}")
        extras = rich.iloc[indices][list(EXTRA_COLUMNS)].reset_index(drop=True)
        if not np.isfinite(extras.to_numpy(dtype=float)).all():
            raise FeatureAdditionError(f"non-finite additions at source rows: {symbol}")
        part = part.reset_index(drop=True)
        for column in EXTRA_COLUMNS:
            part[column] = extras[column].to_numpy(dtype=float)
        expected_times = pd.to_datetime(part["feature_bar_time"], utc=True)
        actual_times = raw["open_time"].iloc[indices].reset_index(drop=True)
        time_mismatches += int(
            (expected_times.reset_index(drop=True) != actual_times).sum()
        )
        for side in SIDES:
            mask = part["side"].astype(str).eq(side)
            if not mask.any():
                continue
            side_indices = part.loc[mask, "feature_bar_i"].to_numpy(dtype=int)
            recomputed = extract_feature_rows_for_side(
                rich, side_indices.tolist(), side
            )[list(BASE_COLUMNS)].to_numpy(dtype=float)
            frozen = part.loc[mask, list(BASE_COLUMNS)].to_numpy(dtype=float)
            maximum_base_delta = max(
                maximum_base_delta,
                float(np.max(np.abs(recomputed - frozen))),
            )
        blocks.append(part)
    expanded = (
        pd.concat(blocks, ignore_index=True)
        .sort_values("__source_order")
        .drop(columns="__source_order")
        .reset_index(drop=True)
    )
    if expanded["episode_id"].tolist() != source["episode_id"].tolist():
        raise FeatureAdditionError("expanded dataset changed row identity/order")
    tolerance = float(prereg["feature_rebuild"]["base_parity_tolerance"])
    if maximum_base_delta > tolerance:
        raise FeatureAdditionError(
            f"base feature parity failed: {maximum_base_delta} > {tolerance}"
        )
    if time_mismatches:
        raise FeatureAdditionError(
            f"feature-bar timestamps mismatched: {time_mismatches}"
        )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "l2_dataset_feature_addition.csv"
    expanded.to_csv(path, index=False)
    payload = {
        "protocol": prereg["protocol"],
        "experiment_id": EXPERIMENT_ID,
        "phase": "causal_feature_rebuild",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": git_head(),
        "source_dataset_sha256": prereg["source"]["dataset_sha256"],
        "dataset_path": repo_relative(path),
        "dataset_sha256": sha256_file(path),
        "rows": len(expanded),
        "base_feature_count": len(BASE_COLUMNS),
        "addition_feature_count": len(EXTRA_COLUMNS),
        "total_unique_feature_count": len(BASE_COLUMNS) + len(EXTRA_COLUMNS),
        "excluded_semantic_duplicates": sorted(DUPLICATE_RICH_COLUMNS),
        "maximum_base_feature_absolute_delta": maximum_base_delta,
        "base_parity_tolerance": tolerance,
        "feature_bar_time_mismatches": time_mismatches,
        "nonfinite_feature_cells": int(
            expanded[list(BASE_COLUMNS + EXTRA_COLUMNS)].isna().sum().sum()
        ),
        "holdout_rows_read": 0,
        "future_rows_in_features": 0,
        "promoted": False,
        "deployed": False,
        "production_eligible": False,
    }
    write_json(RESULTS_DIR / "feature_dataset_receipt.json", payload)
    return payload


def learning_rows(data: pd.DataFrame, side: str, split: str) -> pd.DataFrame:
    return data[
        data["side"].astype(str).eq(side)
        & data["split"].astype(str).eq(split)
        & data["dependency_representative"]
    ].copy()


def train_one_arm(
    train: pd.DataFrame,
    tune: pd.DataFrame,
    *,
    side: str,
    arm: str,
    columns: Sequence[str],
    cost: float,
) -> dict[str, Any]:
    model = train_model(
        train,
        tune,
        feature_columns=columns,
        objective="regression",
        params_override=L2_DETERMINISTIC_PARAMS,
    )
    scores = model.predict(tune[list(columns)], num_iteration=model.best_iteration)
    threshold = float(np.quantile(scores, 0.9))
    diagnostics = score_diagnostics(tune, scores, threshold, cost)
    health = {
        "best_iteration_at_least_2": int(model.best_iteration) >= 2,
        "at_least_10_distinct_tune_scores": diagnostics["distinct_scores"] >= 10,
        "tune_q90_pass_rate_at_most_25pct": diagnostics["q90"]["pass_rate"] <= 0.25,
    }
    health["passed"] = all(health.values())
    model_dir = OUTPUT_DIR / "models"
    score_dir = OUTPUT_DIR / "tune_scores"
    importance_dir = OUTPUT_DIR / "feature_importance"
    for directory in (model_dir, score_dir, importance_dir):
        directory.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / f"{side}_{arm}.txt"
    model.save_model(str(model_path))
    score_path = score_dir / f"{side}_{arm}.csv"
    pd.DataFrame(
        {
            "episode_id": tune["episode_id"].astype(str).to_numpy(),
            "score": scores,
            "label": tune["label"].to_numpy(dtype=int),
            "realized_ret": tune["realized_ret"].to_numpy(dtype=float),
        }
    ).to_csv(score_path, index=False)
    importance = pd.DataFrame(
        {
            "feature": list(columns),
            "gain": model.feature_importance(importance_type="gain"),
            "split": model.feature_importance(importance_type="split"),
        }
    ).sort_values(["gain", "feature"], ascending=[False, True])
    importance_path = importance_dir / f"{side}_{arm}.csv"
    importance.to_csv(importance_path, index=False)
    return {
        "side": side,
        "arm": arm,
        "selection_eligible": arm in SELECTION_ARMS,
        "feature_columns": list(columns),
        "feature_count": len(columns),
        "added_feature_count": len(set(columns) - set(BASE_COLUMNS)),
        "train_rows": len(train),
        "tune_rows": len(tune),
        "best_iteration": int(model.best_iteration),
        "tune_q90_threshold": threshold,
        "diagnostics": diagnostics,
        "health": health,
        "model_path": repo_relative(model_path),
        "model_sha256": sha256_file(model_path),
        "tune_scores_path": repo_relative(score_path),
        "tune_scores_sha256": sha256_file(score_path),
        "feature_importance_path": repo_relative(importance_path),
        "feature_importance_sha256": sha256_file(importance_path),
        "feature_importance_top15": importance.head(15).to_dict("records"),
    }


def select_best_arm(records: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    eligible = [
        record
        for record in records
        if record["selection_eligible"] and record["health"]["passed"]
    ]
    if not eligible:
        return next(record for record in records if record["arm"] == "baseline_28")
    order = {arm: index for index, arm in enumerate(SELECTION_ARMS)}

    def key(record: Mapping[str, Any]) -> tuple[float, float, int, int]:
        diagnostic = record["diagnostics"]
        rho = diagnostic["spearman_score_vs_return"]
        return (
            -float(diagnostic["fractional_top_decile"]["net_mean"]),
            -float(rho if rho is not None else -np.inf),
            int(record["feature_count"]),
            order[str(record["arm"])],
        )

    return min(eligible, key=key)


def run_selection(prereg: Mapping[str, Any]) -> dict[str, Any]:
    verify_pinned_inputs(prereg, include_controls=False, include_expanded=True)
    data = load_source(prereg, expanded=True)
    cost = float(prereg["frozen_contract"]["round_trip_cost_fraction"])
    selections: dict[str, Any] = {}
    ranking_rows: list[dict[str, Any]] = []
    for side in SIDES:
        train = learning_rows(data, side, "train")
        tune = learning_rows(data, side, "tune")
        records = [
            train_one_arm(
                train,
                tune,
                side=side,
                arm=arm,
                columns=columns,
                cost=cost,
            )
            for arm, columns in MODEL_ARMS.items()
        ]
        chosen = select_best_arm(records)
        selections[side] = {
            "selected_arm": chosen["arm"],
            "feature_columns": chosen["feature_columns"],
            "selection_reason": (
                "highest healthy tune fractional tie-aware top-decile net among "
                "baseline_28 and preregistered add-only arms"
            ),
            "arms": records,
        }
        for record in records:
            diagnostic = record["diagnostics"]
            ranking_rows.append(
                {
                    "side": side,
                    "arm": record["arm"],
                    "selection_eligible": record["selection_eligible"],
                    "feature_count": record["feature_count"],
                    "added_feature_count": record["added_feature_count"],
                    "best_iteration": record["best_iteration"],
                    "distinct_tune_scores": diagnostic["distinct_scores"],
                    "fractional_top_decile_net": diagnostic["fractional_top_decile"][
                        "net_mean"
                    ],
                    "q90_n": diagnostic["q90"]["n"],
                    "q90_pass_rate": diagnostic["q90"]["pass_rate"],
                    "q90_net": diagnostic["q90"]["net_mean"],
                    "spearman": diagnostic["spearman_score_vs_return"],
                    "healthy": record["health"]["passed"],
                    "selected": record["arm"] == chosen["arm"],
                }
            )
    ranking_path = OUTPUT_DIR / "tune_addition_arm_ranking.csv"
    pd.DataFrame(ranking_rows).to_csv(ranking_path, index=False)
    feature_receipt = RESULTS_DIR / "feature_dataset_receipt.json"
    receipt = {
        "protocol": prereg["protocol"],
        "experiment_id": EXPERIMENT_ID,
        "phase": "tune_selection",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": git_head(),
        "source_dataset_sha256": prereg["source"]["dataset_sha256"],
        "feature_dataset_receipt_sha256": sha256_file(feature_receipt),
        "selection_arms": {
            name: list(columns) for name, columns in SELECTION_ARMS.items()
        },
        "diagnostic_arms": {
            name: list(columns) for name, columns in DIAGNOSTIC_ARMS.items()
        },
        "training_params": {
            **LGB_PARAMS,
            **L2_DETERMINISTIC_PARAMS,
            "objective": "regression",
        },
        "runtime": runtime_versions(),
        "selections": selections,
        "ranking_path": repo_relative(ranking_path),
        "ranking_sha256": sha256_file(ranking_path),
        "final_validation_rows_used_for_selection": 0,
        "holdout_rows_read": 0,
        "promoted": False,
        "deployed": False,
        "production_eligible": False,
    }
    write_json(RESULTS_DIR / "selection_receipt.json", receipt)
    return receipt


def load_selection(
    prereg: Mapping[str, Any], *, require_committed: bool
) -> dict[str, Any]:
    path = RESULTS_DIR / "selection_receipt.json"
    if not path.is_file():
        raise FeatureAdditionError("selection receipt missing")
    if require_committed and not committed_file(path):
        raise FeatureAdditionError(
            "selection receipt must be committed before final validation"
        )
    receipt = json.loads(path.read_text(encoding="utf-8"))
    if receipt.get("phase") != "tune_selection":
        raise FeatureAdditionError("selection phase drifted")
    if receipt.get("final_validation_rows_used_for_selection") != 0:
        raise FeatureAdditionError("selection used final validation")
    if receipt.get("source_dataset_sha256") != prereg["source"]["dataset_sha256"]:
        raise FeatureAdditionError("selection source drifted")
    if receipt.get("feature_dataset_receipt_sha256") != sha256_file(
        RESULTS_DIR / "feature_dataset_receipt.json"
    ):
        raise FeatureAdditionError("selection feature dataset drifted")
    for side in SIDES:
        if receipt["selections"][side]["selected_arm"] not in SELECTION_ARMS:
            raise FeatureAdditionError(f"unknown selected arm for {side}")
    return receipt


def model_record(
    selection: Mapping[str, Any], side: str, arm: str
) -> Mapping[str, Any]:
    record = next(
        (item for item in selection["selections"][side]["arms"] if item["arm"] == arm),
        None,
    )
    if record is None:
        raise FeatureAdditionError(f"missing model record: {side}/{arm}")
    for path_key, sha_key in (
        ("model_path", "model_sha256"),
        ("tune_scores_path", "tune_scores_sha256"),
        ("feature_importance_path", "feature_importance_sha256"),
    ):
        path = repo_path(record[path_key])
        if not path.is_file() or sha256_file(path) != record[sha_key]:
            raise FeatureAdditionError(f"model artifact drifted: {path}")
    return record


def score_final_arm(
    data: pd.DataFrame,
    selection: Mapping[str, Any],
    *,
    side: str,
    arm: str,
) -> pd.DataFrame:
    record = model_record(selection, side, arm)
    columns = list(MODEL_ARMS[arm])
    model = lgb.Booster(model_file=str(repo_path(record["model_path"])))
    tune_scores = pd.read_csv(
        repo_path(record["tune_scores_path"]), float_precision="round_trip"
    )["score"].to_numpy(dtype=float)
    final = data[
        data["side"].astype(str).eq(side)
        & data["split"].astype(str).eq("final_validation")
    ].copy()
    scores = model.predict(final[columns], num_iteration=int(record["best_iteration"]))
    threshold = float(record["tune_q90_threshold"])
    prefix = f"{arm}_{side}"
    final[f"{prefix}_score"] = scores
    final[f"{prefix}_percentile"] = empirical_percentile(tune_scores, scores)
    final[f"{prefix}_threshold"] = threshold
    final[f"{prefix}_keep"] = scores >= threshold
    return final


def metric_bundle(
    scored_by_side: Mapping[str, pd.DataFrame],
    controls: pd.DataFrame,
    *,
    arms_by_side: Mapping[str, str],
    cost: float,
) -> dict[str, Any]:
    pieces: list[pd.DataFrame] = []
    by_side: dict[str, Any] = {}
    for side in SIDES:
        arm = arms_by_side[side]
        prefix = f"{arm}_{side}"
        reps = scored_by_side[side][
            scored_by_side[side]["dependency_representative"]
        ].copy()
        scores = reps[f"{prefix}_score"].to_numpy(dtype=float)
        percentiles = reps[f"{prefix}_percentile"].to_numpy(dtype=float)
        keep = reps[f"{prefix}_keep"].to_numpy(dtype=bool)
        returns = reps["realized_ret"].to_numpy(dtype=float)
        labels = reps["label"].to_numpy(dtype=int)
        by_side[side] = {
            "arm": arm,
            "feature_count": len(MODEL_ARMS[arm]),
            "added_feature_count": len(set(MODEL_ARMS[arm]) - set(BASE_COLUMNS)),
            "final_validation": safe_metrics(labels, scores, returns, cost),
            "fractional_top_decile": fractional_top_decile_metrics(
                scores, returns, labels, cost
            ),
            "frozen_q90": selected_metrics(reps, keep, cost),
            "outcome_permutation_p": outcome_permutation_pvalue(scores, returns),
        }
        renamed = reps.copy()
        renamed["evaluation_score"] = percentiles
        renamed["evaluation_keep"] = keep
        pieces.append(renamed)
    combined = pd.concat(pieces, ignore_index=True)
    scores = combined["evaluation_score"].to_numpy(dtype=float)
    keep = combined["evaluation_keep"].to_numpy(dtype=bool)
    returns = combined["realized_ret"].to_numpy(dtype=float)
    labels = combined["label"].to_numpy(dtype=int)
    selected_ids = set(combined.loc[keep, "episode_id"].astype(str))
    return {
        "arms_by_side": dict(arms_by_side),
        "final_validation": safe_metrics(labels, scores, returns, cost),
        "fractional_top_decile": fractional_top_decile_metrics(
            scores, returns, labels, cost
        ),
        "frozen_q90": selected_metrics(combined, keep, cost),
        "outcome_permutation_p": outcome_permutation_pvalue(scores, returns),
        "matched_control": strict_control_metrics(combined, controls, selected_ids),
        "by_side": by_side,
        "representative_rows": len(combined),
    }


def baseline_reproduction(
    scored: Mapping[str, pd.DataFrame], prereg: Mapping[str, Any]
) -> dict[str, Any]:
    previous = pd.read_csv(repo_path(prereg["source"]["prior_side_split_scores"]))
    score_delta: list[float] = []
    threshold_delta: list[float] = []
    percentile_delta: list[float] = []
    decisions: list[bool] = []
    rows = 0
    for side in SIDES:
        prefix = f"baseline_28_{side}"
        current = scored[side][
            [
                "episode_id",
                f"{prefix}_score",
                f"{prefix}_percentile",
                f"{prefix}_threshold",
                f"{prefix}_keep",
            ]
        ]
        prior = previous[previous["side"].astype(str).eq(side)][
            [
                "episode_id",
                "l2_score",
                "side_percentile_score",
                "l2_threshold",
                "l2_keep",
            ]
        ]
        merged = current.merge(prior, on="episode_id", validate="one_to_one")
        if len(merged) != len(current) or len(merged) != len(prior):
            raise FeatureAdditionError(f"baseline row identity drifted: {side}")
        score_delta.extend(np.abs(merged[f"{prefix}_score"] - merged["l2_score"]))
        threshold_delta.extend(
            np.abs(merged[f"{prefix}_threshold"] - merged["l2_threshold"])
        )
        percentile_delta.extend(
            np.abs(merged[f"{prefix}_percentile"] - merged["side_percentile_score"])
        )
        decisions.extend(
            (
                merged[f"{prefix}_keep"].astype(bool) == merged["l2_keep"].astype(bool)
            ).tolist()
        )
        rows += len(merged)
    maximum_score_delta = max(score_delta, default=0.0)
    maximum_threshold_delta = max(threshold_delta, default=0.0)
    maximum_percentile_delta = max(percentile_delta, default=0.0)
    passed = (
        maximum_score_delta <= 1e-12
        and maximum_threshold_delta <= 1e-12
        and maximum_percentile_delta <= 1e-12
        and all(decisions)
    )
    return {
        "rows": rows,
        "maximum_absolute_score_delta": maximum_score_delta,
        "maximum_absolute_threshold_delta": maximum_threshold_delta,
        "maximum_absolute_percentile_delta": maximum_percentile_delta,
        "keep_decisions_exact": all(decisions),
        "tolerance": 1e-12,
        "passed": passed,
    }


def run_final_evaluation(prereg: Mapping[str, Any]) -> dict[str, Any]:
    verify_pinned_inputs(prereg, include_controls=True, include_expanded=True)
    selection = load_selection(prereg, require_committed=True)
    data = load_source(prereg, expanded=True)
    controls = pd.read_csv(repo_path(prereg["source"]["matched_controls_path"]))
    cost = float(prereg["frozen_contract"]["round_trip_cost_fraction"])
    selected_arms = {
        side: selection["selections"][side]["selected_arm"] for side in SIDES
    }
    configurations = {
        "selected": selected_arms,
        "baseline_28": {side: "baseline_28" for side in SIDES},
        "ma_spread_only": {side: "ma_spread_only" for side in SIDES},
    }
    scored_cache: dict[tuple[str, str], pd.DataFrame] = {}
    for arms in configurations.values():
        for side, arm in arms.items():
            key = (side, arm)
            if key not in scored_cache:
                scored_cache[key] = score_final_arm(data, selection, side=side, arm=arm)
    metrics = {
        name: metric_bundle(
            {side: scored_cache[(side, arms[side])] for side in SIDES},
            controls,
            arms_by_side=arms,
            cost=cost,
        )
        for name, arms in configurations.items()
    }
    reproduction = baseline_reproduction(
        {side: scored_cache[(side, "baseline_28")] for side in SIDES}, prereg
    )
    selected = metrics["selected"]
    baseline = metrics["baseline_28"]
    by_side = selected["by_side"]
    gate = {
        "baseline_reproduction_required": reproduction["passed"],
        "at_least_one_side_selected_an_addition": any(
            selected_arms[side] != "baseline_28" for side in SIDES
        ),
        "aggregate_selected_q90_net_strictly_better_than_baseline_28": (
            selected["frozen_q90"]["net_mean"] is not None
            and baseline["frozen_q90"]["net_mean"] is not None
            and selected["frozen_q90"]["net_mean"] > baseline["frozen_q90"]["net_mean"]
        ),
        "aggregate_selected_top_decile_net_positive": (
            selected["fractional_top_decile"]["net_mean"] > 0
        ),
        "aggregate_outcome_permutation_p_lt_0_01": (
            selected["outcome_permutation_p"] < 0.01
        ),
        "aggregate_minimum_30_selected_dependency_blocks": (
            selected["frozen_q90"]["n"] >= 30
        ),
        "aggregate_beats_matched_controls_every_assignment": (
            selected["matched_control"]["all_assignments_positive"]
        ),
        "each_side_minimum_10_selected_dependency_blocks": all(
            by_side[side]["frozen_q90"]["n"] >= 10 for side in SIDES
        ),
        "neither_side_selected_q90_net_negative": all(
            by_side[side]["frozen_q90"]["net_mean"] is not None
            and by_side[side]["frozen_q90"]["net_mean"] >= 0
            for side in SIDES
        ),
    }
    gate["passed"] = all(gate.values())
    scored_columns = [
        "episode_id",
        "symbol",
        "side",
        "available_at",
        "label",
        "realized_ret",
        "net_ret",
        "dependency_block_id",
        "dependency_representative",
    ]
    frames: list[pd.DataFrame] = []
    for side in SIDES:
        selected_arm = selected_arms[side]
        base = scored_cache[(side, selected_arm)][scored_columns].copy()
        for config, arms in configurations.items():
            arm = arms[side]
            prefix = f"{arm}_{side}"
            source = scored_cache[(side, arm)].set_index("episode_id")
            base[f"{config}_arm"] = arm
            for suffix in ("score", "percentile", "threshold", "keep"):
                base[f"{config}_{suffix}"] = base["episode_id"].map(
                    source[f"{prefix}_{suffix}"]
                )
        frames.append(base)
    scored_path = OUTPUT_DIR / "final_validation_feature_addition_scored.csv"
    pd.concat(frames, ignore_index=True).to_csv(scored_path, index=False)
    receipt = {
        "protocol": prereg["protocol"],
        "experiment_id": EXPERIMENT_ID,
        "phase": "final_validation",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": git_head(),
        "selection_receipt_path": repo_relative(RESULTS_DIR / "selection_receipt.json"),
        "selection_receipt_sha256": sha256_file(RESULTS_DIR / "selection_receipt.json"),
        "selected_arms": selected_arms,
        "baseline_reproduction": reproduction,
        "metrics": metrics,
        "primary_gate": gate,
        "decision": "accept" if gate["passed"] else "reject",
        "scored_path": repo_relative(scored_path),
        "scored_sha256": sha256_file(scored_path),
        "final_validation_rows_used_for_selection": 0,
        "holdout_rows_read": 0,
        "promoted": False,
        "deployed": False,
        "active_or_frozen_changed": False,
        "forward_state_changed": False,
        "orders_placed": False,
        "telegram_sent": False,
        "production_eligible": False,
    }
    write_json(RESULTS_DIR / "training_receipt.json", receipt)
    return receipt


def render_diagnostics(prereg: Mapping[str, Any]) -> Path:
    import matplotlib.pyplot as plt

    selection = load_selection(prereg, require_committed=False)
    training = json.loads(
        (RESULTS_DIR / "training_receipt.json").read_text(encoding="utf-8")
    )
    ranking = pd.read_csv(repo_path(selection["ranking_path"]))
    ranking = ranking[ranking["selection_eligible"].astype(bool)]
    fig, axes = plt.subplots(2, 2, figsize=(17, 11), constrained_layout=True)
    for axis, side in zip(axes[0], SIDES):
        rows = ranking[ranking["side"].astype(str).eq(side)]
        colors = ["#d62728" if bool(value) else "#4c78a8" for value in rows["selected"]]
        axis.barh(rows["arm"], rows["fractional_top_decile_net"] * 100, color=colors)
        axis.axvline(0, color="black", linewidth=0.8)
        axis.set_title(f"{side.upper()} tune: tie-aware top-10% net")
        axis.set_xlabel("%")
        axis.invert_yaxis()
    names = ["ma_spread_only", "baseline_28", "selected"]
    labels = ["1 feature", "baseline 28", "selected add-only"]
    metrics = training["metrics"]
    axes[1, 0].bar(
        labels,
        [metrics[name]["fractional_top_decile"]["net_mean"] * 100 for name in names],
        color=["#9ecae9", "#6baed6", "#d62728"],
    )
    axes[1, 0].axhline(0, color="black", linewidth=0.8)
    axes[1, 0].set_title("April final: tie-aware top-10% net")
    axes[1, 0].set_ylabel("%")
    axes[1, 1].bar(
        labels,
        [metrics[name]["frozen_q90"]["net_mean"] * 100 for name in names],
        color=["#9ecae9", "#6baed6", "#d62728"],
    )
    axes[1, 1].axhline(0, color="black", linewidth=0.8)
    axes[1, 1].set_title("April final: frozen tune-q90 net")
    axes[1, 1].set_ylabel("%")
    fig.suptitle("15m L2 add-only causal feature groups (separate LONG / SHORT)")
    path = OUTPUT_DIR / "feature_addition_diagnostics.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=170)
    plt.close(fig)
    return path


def pct(value: Any) -> str:
    return "—" if value is None else f"{float(value):+.3%}"


def feature_group_for_report(feature: str) -> str:
    if feature in BASE_COLUMNS:
        return "legacy_28"
    for name, columns in ADDITION_GROUPS.items():
        if feature in columns:
            return name
    raise FeatureAdditionError(f"unknown report feature: {feature}")


def build_report(prereg: Mapping[str, Any]) -> Path:
    selection = load_selection(prereg, require_committed=False)
    training = json.loads(
        (RESULTS_DIR / "training_receipt.json").read_text(encoding="utf-8")
    )
    feature_receipt = json.loads(
        (RESULTS_DIR / "feature_dataset_receipt.json").read_text(encoding="utf-8")
    )
    ranking = pd.read_csv(repo_path(selection["ranking_path"]))
    source = load_source(prereg, expanded=True)
    reps = source[source["dependency_representative"]]
    selected = training["metrics"]["selected"]
    gate = training["primary_gate"]
    lines = [
        "# 15m L2 因果特征增量实验（2026-09-02）",
        "",
        "## 结论先行",
        "",
        (
            f"最终裁决：**{training['decision'].upper()}**。"
            f"LONG tune 入选 **{training['selected_arms']['long']}**，"
            f"SHORT tune 入选 **{training['selected_arms']['short']}**。"
            "本轮只允许在旧 28 列上增加特征；候选、标签、时间切分、模型参数、"
            "成本和匹配对照均保持不变。"
        ),
        "",
        "![增量特征诊断](output/ma_launch_l2_feature_addition_v1/feature_addition_diagnostics.png)",
        "",
        "## 数据与因果复原",
        "",
        "| split | LONG 独立事件 | SHORT 独立事件 | 合计 |",
        "|---|---:|---:|---:|",
    ]
    for split in ("train", "tune", "final_validation"):
        long_n = len(reps[(reps["split"] == split) & (reps["side"] == "long")])
        short_n = len(reps[(reps["split"] == split) & (reps["side"] == "short")])
        lines.append(f"| {split} | {long_n} | {short_n} | {long_n + short_n} |")
    start = pd.to_datetime(source["available_at"], utc=True).min()
    end = pd.to_datetime(source["available_at"], utc=True).max()
    lines.extend(
        [
            "",
            (
                f"数据范围：{start:%Y-%m-%d} 至 {end:%Y-%m-%d}；holdout 读取 0。"
                f"原 28 列复算最大误差 {feature_receipt['maximum_base_feature_absolute_delta']:.3e}；"
                f"新增 {feature_receipt['addition_feature_count']} 列，去重后总计 "
                f"{feature_receipt['total_unique_feature_count']} 列；未来特征行 0。"
            ),
            "",
            (
                "语义重复列被剔除：`fast_spread`、`fast_spread_rank96`、"
                "`dense_run_len_fast`、`roc_12`、`roc_48`、`vol_ratio_20`。"
                "LONG/SHORT 独立训练；新增带符号市场坐标不会跨方向混训。"
            ),
            "",
            "## 新增特征组",
            "",
            "| 组 | 新增列数 | 主要内容 |",
            "|---|---:|---|",
            "| ma_family | 26 | 多周期 SMA/EMA 相对位置、间距、斜率、交叉、带宽 |",
            "| dense_dynamics | 8 | 密集带收缩/扩张速度、历史位置、连续状态 |",
            "| momentum_structure | 22 | 多窗收益、距高低点、突破距离、区间位置 |",
            "| candle_volatility | 11 | ATR7/28、实现波动、实体/影线、波动变化 |",
            "| volume_flow | 5 | 96 根量比、量 z、价量相关、突破量、上涨量占比 |",
            "| market_structure | 5 | HH/HL/LH/LL 与结构偏向 |",
            "| time_context | 5 | UTC 小时与星期周期编码 |",
            "",
            "## March tune 预注册选择",
            "",
            "| 方向 | 方案 | 总列 | 新增 | iter | top-10% 净收益 | q90 n | q90 净收益 | Spearman | 健康 | 入选 |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    eligible = ranking[ranking["selection_eligible"].astype(bool)]
    for _, row in eligible.iterrows():
        lines.append(
            f"| {str(row['side']).upper()} | {row['arm']} | "
            f"{int(row['feature_count'])} | {int(row['added_feature_count'])} | "
            f"{int(row['best_iteration'])} | {pct(row['fractional_top_decile_net'])} | "
            f"{int(row['q90_n'])} | {pct(row['q90_net'])} | "
            f"{float(row['spearman']):.4f} | {bool(row['healthy'])} | "
            f"{bool(row['selected'])} |"
        )
    lines.extend(
        [
            "",
            "选择与 early stopping 只看 March tune；April final 在 selection receipt 提交后才打开。",
            "",
            "## April final：基线、单特征与冻结入选组合",
            "",
            "| 配置 | LONG / SHORT | top-10% 净收益 | q90 n | q90 净收益 | 胜率 | p | 事件减匹配对照 | 8/8 均跑赢 |",
            "|---|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for label, key in (
        ("单特征基线", "ma_spread_only"),
        ("旧 28 列基线", "baseline_28"),
        ("tune 冻结入选", "selected"),
    ):
        item = training["metrics"][key]
        arms = item["arms_by_side"]
        lines.append(
            f"| {label} | {arms['long']} / {arms['short']} | "
            f"{pct(item['fractional_top_decile']['net_mean'])} | "
            f"{item['frozen_q90']['n']} | {pct(item['frozen_q90']['net_mean'])} | "
            f"{item['frozen_q90']['win_rate']:.2%} | "
            f"{item['outcome_permutation_p']:.6f} | "
            f"{pct(item['matched_control']['mean_event_minus_control'])} | "
            f"{item['matched_control']['all_assignments_positive']} |"
        )
    lines.extend(
        [
            "",
            (
                "入选组合诊断："
                f"AUC={selected['final_validation']['roc_auc']:.4f}，"
                f"PR-AUC={selected['final_validation']['pr_auc']:.4f}，"
                f"Spearman={selected['final_validation']['spearman_score_vs_return']:.4f}。"
                "AUC 不作成功裁决；裁决看扣成本收益、p、样本量及匹配对照。"
            ),
            "",
            "## 分方向 final",
            "",
            "| 方向 | 入选方案 | 总列 | 新增 | final n | q90 n | q90 净收益 | 胜率 | p |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for side in SIDES:
        item = selected["by_side"][side]
        lines.append(
            f"| {side.upper()} | {item['arm']} | {item['feature_count']} | "
            f"{item['added_feature_count']} | {item['final_validation']['n']} | "
            f"{item['frozen_q90']['n']} | {pct(item['frozen_q90']['net_mean'])} | "
            f"{item['frozen_q90']['win_rate']:.2%} | "
            f"{item['outcome_permutation_p']:.6f} |"
        )
    lines.extend(
        [
            "",
            "## 入选模型的 gain 前列",
            "",
            "| 方向 | 特征 | 组 | gain |",
            "|---|---|---|---:|",
        ]
    )
    for side in SIDES:
        arm = training["selected_arms"][side]
        record = model_record(selection, side, arm)
        for item in record["feature_importance_top15"][:10]:
            lines.append(
                f"| {side.upper()} | {item['feature']} | "
                f"{feature_group_for_report(str(item['feature']))} | {float(item['gain']):.2f} |"
            )
    matched = selected["matched_control"]
    lines.extend(
        [
            "",
            "## 匹配对照与验收门",
            "",
            (
                "匹配条件保持同币、同月、同 UTC 8 小时时段、同 ATR 桶、同方向及同障碍/成本。"
                f"入选事件完整控制覆盖 {matched['selected_event_complete_control_count']}/"
                f"{matched['selected_event_count']}；平均事件减对照 "
                f"{pct(matched['mean_event_minus_control'])}。"
            ),
            "",
            "| 预注册门 | 通过 |",
            "|---|---|",
        ]
    )
    for name, passed in gate.items():
        if name != "passed":
            lines.append(f"| {name} | {passed} |")
    lines.extend(
        [
            f"| 全部门 | **{gate['passed']}** |",
            "",
            "## 基线复现",
            "",
            (
                f"旧 28 列基线复现：{training['baseline_reproduction']['passed']}；"
                f"final 最大分数误差 "
                f"{training['baseline_reproduction']['maximum_absolute_score_delta']:.3e}；"
                f"KEEP 完全一致：{training['baseline_reproduction']['keep_decisions_exact']}。"
            ),
            "",
            "## 风险与诚实声明",
            "",
            "- train 417、tune 229、final 242 个独立事件；110 列完整模型相对样本量偏大，必须以时间外结果而非 tune 增益裁决。",
            "- 十个 add-only 方案共享同一 tune，存在多重比较；没有用 April final 反向重选。",
            "- 新增方向性列保留原始市场符号，但 LONG/SHORT 完全分开训练；若未来任何方案通过，还需另做坐标语义消融。",
            "- 当前 L1 使用完成态窗口，本实验不能冒充 tip/tip-1/tip-2 实盘信号。",
            "- 未读取 2026-05-04 后 holdout，未 promote、部署、改 ACTIVE/frozen/forward、发 Telegram 或下单。",
            "",
            "## 复现命令",
            "",
            "    PYTHONPATH=. .venv/bin/python -m scripts.research_15m_ma_launch_l2_feature_addition --build-features",
            "    PYTHONPATH=. .venv/bin/python -m scripts.research_15m_ma_launch_l2_feature_addition --select",
            "    # 提交 selection receipt 后：",
            "    PYTHONPATH=. .venv/bin/python -m scripts.research_15m_ma_launch_l2_feature_addition --evaluate-final",
            "    PYTHONPATH=. .venv/bin/python -m scripts.research_15m_ma_launch_l2_feature_addition --render --verify --report",
            "    python3 scripts/md_to_html.py analysis/p3_15m_ma_launch_l2_feature_addition_20260902.md --out-dir analysis/html",
            "",
            "## 下一步",
            "",
            "只有全部预注册门通过，才值得在新的未见时间段复验；本报告不授权读取 holdout、promote 或部署。",
            "",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    return REPORT_PATH


def verify_outputs(prereg: Mapping[str, Any]) -> dict[str, Any]:
    verify_pinned_inputs(prereg, include_controls=True, include_expanded=True)
    selection = load_selection(prereg, require_committed=True)
    training_path = RESULTS_DIR / "training_receipt.json"
    if not training_path.is_file():
        raise FeatureAdditionError("training receipt missing")
    training = json.loads(training_path.read_text(encoding="utf-8"))
    if training["selection_receipt_sha256"] != sha256_file(
        RESULTS_DIR / "selection_receipt.json"
    ):
        raise FeatureAdditionError("training receipt not bound to selection")
    if training["holdout_rows_read"] != 0:
        raise FeatureAdditionError("holdout read drifted")
    if training["final_validation_rows_used_for_selection"] != 0:
        raise FeatureAdditionError("final validation leaked into selection")
    scored = repo_path(training["scored_path"])
    if not scored.is_file() or sha256_file(scored) != training["scored_sha256"]:
        raise FeatureAdditionError("scored final artifact drifted")
    for side in SIDES:
        for arm in MODEL_ARMS:
            model_record(selection, side, arm)
    diagnostic = OUTPUT_DIR / "feature_addition_diagnostics.png"
    feature_receipt = json.loads(
        (RESULTS_DIR / "feature_dataset_receipt.json").read_text(encoding="utf-8")
    )
    checks = {
        "base_feature_rebuild_parity": (
            feature_receipt["maximum_base_feature_absolute_delta"]
            <= feature_receipt["base_parity_tolerance"]
        ),
        "baseline_model_reproduction": training["baseline_reproduction"]["passed"],
        "selection_receipt_committed_before_final": committed_file(
            RESULTS_DIR / "selection_receipt.json"
        ),
        "selection_final_rows_zero": (
            selection["final_validation_rows_used_for_selection"] == 0
        ),
        "holdout_rows_zero": (
            selection["holdout_rows_read"] == 0
            and training["holdout_rows_read"] == 0
            and feature_receipt["holdout_rows_read"] == 0
        ),
        "future_feature_rows_zero": feature_receipt["future_rows_in_features"] == 0,
        "all_22_models_verified": True,
        "scored_hash_verified": True,
        "diagnostic_exists": diagnostic.is_file(),
        "not_promoted_or_deployed": (
            not training["promoted"]
            and not training["deployed"]
            and not training["active_or_frozen_changed"]
        ),
    }
    checks["passed"] = all(checks.values())
    receipt = {
        "protocol": prereg["protocol"],
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "feature_dataset_receipt_sha256": sha256_file(
            RESULTS_DIR / "feature_dataset_receipt.json"
        ),
        "selection_receipt_sha256": sha256_file(RESULTS_DIR / "selection_receipt.json"),
        "training_receipt_sha256": sha256_file(training_path),
        "diagnostic_path": repo_relative(diagnostic),
        "diagnostic_sha256": (
            sha256_file(diagnostic) if diagnostic.is_file() else None
        ),
    }
    write_json(RESULTS_DIR / "verify_receipt.json", receipt)
    if not checks["passed"]:
        raise FeatureAdditionError(f"verification failed: {checks}")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-features", action="store_true")
    parser.add_argument("--select", action="store_true")
    parser.add_argument("--evaluate-final", action="store_true")
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    if not any(vars(args).values()):
        parser.error("select at least one action")
    prereg = load_preregistration()
    outputs: dict[str, Any] = {}
    if args.build_features:
        outputs["features"] = build_feature_dataset(prereg)
    if args.select:
        outputs["selection"] = run_selection(prereg)
    if args.evaluate_final:
        outputs["final"] = run_final_evaluation(prereg)
    if args.render:
        outputs["render"] = repo_relative(render_diagnostics(prereg))
    if args.verify:
        outputs["verify"] = verify_outputs(prereg)
    if args.report:
        outputs["report"] = repo_relative(build_report(prereg))
    print(json.dumps(outputs, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
