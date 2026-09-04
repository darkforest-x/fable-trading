#!/usr/bin/env python3
"""Evaluate a high-recall 15m launch detector with a causal trend-runner L2.

The L1 detector uses EMA30 launch states and a three-false-bar rearm rule.  It
is intentionally broad enough to show every structural transition.  L2 uses
only values available at the completed signal bar: OHLCV through ``t``, ATR14,
HL2 moving averages through ``t``, prior-only ranges, and fields emitted by L1.
Entry is the next bar open.  Only label/execution resolution reads future bars.

Selection is intentionally honest about hypothesis reuse: the Huber objective
and 97.5th percentile were proposed after inspecting 2023/2024.  The selection
phase freezes a 2023-fitted model and an absolute threshold from 2024 without
touching 2025 outcomes.  The committed receipt is required before the one-shot
2025-01 through 2026-02 pre-holdout validation can run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

import lightgbm as lgb
import numpy as np
import pandas as pd

from scripts.backtest_two_key_candle_pine_v8_btc_1h import signflip_p
from scripts.research_btcusdtp_15m_dual_ma_runner import (
    BAR_DELTA,
    _assignment_metrics,
    evaluate,
    load_base,
    matched_controls,
)
from scripts.research_btcusdtp_15m_ma_state_trend import (
    fold_label,
    json_value,
    metrics,
    utc,
    write_csv,
    write_json,
)
from scripts.research_two_key_candle_ma_retest_1h import pine_rma, sha256_file


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-btcusdtp-15m-high-recall-l2-trend-runner-preholdout-20260904-v1"
EXPERIMENT = ROOT / "experiments" / "active" / EXPERIMENT_ID
CONFIG_PATH = EXPERIMENT / "config.json"
RESULTS = EXPERIMENT / "results"
SELECTION_RECEIPT = RESULTS / "selection_receipt.json"
MODEL_PATH = RESULTS / "l2_huber_model.txt"
MODEL_CONTRACT_PATH = RESULTS / "model_contract.json"
SCRIPT_PATH = Path(__file__).resolve()
V2_CONFIG_PATH = (
    ROOT
    / "experiments/active/exp-btcusdtp-15m-dual-ma-runner-preholdout-20260904-v1/config.json"
)


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def load_v2_config() -> dict[str, Any]:
    return json.loads(V2_CONFIG_PATH.read_text(encoding="utf-8"))


def l1_params(config: Mapping[str, Any]) -> dict[str, str]:
    return {
        "trigger_reference": str(config["l1"]["trigger_reference"]),
        "dedupe_policy": str(config["l1"]["dedupe_policy"]),
        "runner_policy": str(config["execution"]["runner_policy"]),
    }


def load_base_until(
    config: Mapping[str, Any], end_exclusive: pd.Timestamp
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load the physically pre-holdout source and truncate the analysis frame."""

    compatibility = deepcopy(load_v2_config())
    compatibility["window"]["audit_end_exclusive"] = utc(end_exclusive).isoformat()
    frame, quality = load_base(compatibility)
    if int(quality["holdout_rows_read"]) != 0:
        raise RuntimeError("L2 loader materialized repository holdout")
    if len(frame) and utc(frame["open_time"].max()) >= utc(end_exclusive):
        raise RuntimeError("analysis frame reaches phase end")
    return frame, quality


def add_context_features(
    frame: pd.DataFrame,
    events: pd.DataFrame,
    *,
    cost: float,
) -> pd.DataFrame:
    """Add causal L2 features sampled at each signal bar.

    Source columns are open/high/low/close/volume through signal bar ``t``.
    Windows are EMA20/30, SMA20/30/60/120/160/240 on HL2, ATR/ADX14,
    rolling volume median 20, ATR median 96, range median 20, efficiency
    ratios 12/24, signed returns 8/24, and the previous 24-bar range.  No
    feature reads bar ``t+1`` or any later bar.
    """

    if events.empty:
        return events.copy()
    enriched = frame.copy()
    hl2 = (enriched["high"] + enriched["low"]) / 2.0
    for length in (20, 30, 60, 120, 160, 240):
        enriched[f"sma{length}"] = hl2.rolling(length, min_periods=length).mean()
    enriched["ema20"] = hl2.ewm(span=20, adjust=False, min_periods=20).mean()
    enriched["ema30"] = hl2.ewm(span=30, adjust=False, min_periods=30).mean()

    prior_close = enriched["close"].shift(1)
    true_range = np.maximum(
        enriched["high"] - enriched["low"],
        np.maximum(
            (enriched["high"] - prior_close).abs(),
            (enriched["low"] - prior_close).abs(),
        ),
    )
    up = enriched["high"].diff()
    down = -enriched["low"].diff()
    plus_dm = np.where((up > down) & (up > 0.0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0.0), down, 0.0)
    atr14 = pine_rma(true_range.to_numpy(dtype=float), 14)
    plus_rma = pine_rma(np.asarray(plus_dm, dtype=float), 14)
    minus_rma = pine_rma(np.asarray(minus_dm, dtype=float), 14)
    pdi = 100.0 * plus_rma / atr14
    mdi = 100.0 * minus_rma / atr14
    dx = 100.0 * np.abs(pdi - mdi) / np.maximum(pdi + mdi, 1e-12)
    enriched["adx14"] = pine_rma(dx, 14)
    enriched["di_balance"] = pdi - mdi
    enriched["volume_ratio20"] = enriched["volume"] / enriched["volume"].rolling(
        20, min_periods=20
    ).median()
    enriched["atr_ratio96"] = enriched["atr"] / enriched["atr"].rolling(
        96, min_periods=96
    ).median()
    candle_range = enriched["high"] - enriched["low"]
    enriched["range_ratio20"] = candle_range / candle_range.rolling(
        20, min_periods=20
    ).median()
    changes = enriched["close"].diff().abs()
    enriched["eff12"] = (
        (enriched["close"] - enriched["close"].shift(12)).abs()
        / changes.rolling(12, min_periods=12).sum()
    )
    enriched["eff24"] = (
        (enriched["close"] - enriched["close"].shift(24)).abs()
        / changes.rolling(24, min_periods=24).sum()
    )
    enriched["prior24_range_atr"] = (
        enriched["high"].shift(1).rolling(24, min_periods=24).max()
        - enriched["low"].shift(1).rolling(24, min_periods=24).min()
    ) / enriched["atr"]

    output = events.copy()
    indices = output["signal_i"].astype(int).to_numpy()
    direction = output["direction"].to_numpy(dtype=float)
    atr = enriched.loc[indices, "atr"].to_numpy(dtype=float)
    close = enriched.loc[indices, "close"].to_numpy(dtype=float)
    output["fee_to_risk"] = cost / output["risk_fraction"].to_numpy(dtype=float)
    output["atr_pct"] = atr / close
    for column in (
        "volume_ratio20",
        "atr_ratio96",
        "range_ratio20",
        "eff12",
        "eff24",
        "adx14",
        "prior24_range_atr",
    ):
        output[column] = enriched.loc[indices, column].to_numpy(dtype=float)
    output["signed_di_balance"] = direction * enriched.loc[
        indices, "di_balance"
    ].to_numpy(dtype=float)
    output["ema20_sma60_spread_atr"] = direction * (
        enriched.loc[indices, "ema20"].to_numpy(dtype=float)
        - enriched.loc[indices, "sma60"].to_numpy(dtype=float)
    ) / atr
    output["ema30_sma60_spread_atr"] = direction * (
        enriched.loc[indices, "ema30"].to_numpy(dtype=float)
        - enriched.loc[indices, "sma60"].to_numpy(dtype=float)
    ) / atr
    output["sma60_sma120_spread_atr"] = direction * (
        enriched.loc[indices, "sma60"].to_numpy(dtype=float)
        - enriched.loc[indices, "sma120"].to_numpy(dtype=float)
    ) / atr
    output["sma60_sma160_spread_atr"] = direction * (
        enriched.loc[indices, "sma60"].to_numpy(dtype=float)
        - enriched.loc[indices, "sma160"].to_numpy(dtype=float)
    ) / atr
    output["sma120_sma240_spread_atr"] = direction * (
        enriched.loc[indices, "sma120"].to_numpy(dtype=float)
        - enriched.loc[indices, "sma240"].to_numpy(dtype=float)
    ) / atr
    output["signed_return8_atr"] = direction * (
        close - enriched["close"].shift(8).loc[indices].to_numpy(dtype=float)
    ) / atr
    output["signed_return24_atr"] = direction * (
        close - enriched["close"].shift(24).loc[indices].to_numpy(dtype=float)
    ) / atr
    family = output["signal_family"].astype(str)
    output["family_has_direct"] = family.str.contains("direct", regex=False).astype(float)
    output["family_has_rejection"] = family.str.contains(
        "rejection", regex=False
    ).astype(float)
    output["family_has_coil"] = family.str.contains("coil", regex=False).astype(float)
    return output


def model_params(config: Mapping[str, Any]) -> dict[str, Any]:
    row = config["model"]
    seed = int(row["seed"])
    return {
        "objective": str(row["objective"]),
        "alpha": float(row["alpha"]),
        "n_estimators": int(row["n_estimators"]),
        "learning_rate": float(row["learning_rate"]),
        "num_leaves": int(row["num_leaves"]),
        "max_depth": int(row["max_depth"]),
        "min_child_samples": int(row["min_child_samples"]),
        "reg_lambda": float(row["reg_lambda"]),
        "colsample_bytree": float(row["colsample_bytree"]),
        "subsample": 1.0,
        "verbosity": -1,
        "deterministic": bool(row["deterministic"]),
        "force_col_wise": bool(row["force_col_wise"]),
        "n_jobs": int(row["num_threads"]),
        "random_state": seed,
        "data_random_seed": seed,
        "feature_fraction_seed": seed,
        "bagging_seed": seed,
        "extra_seed": seed,
    }


def matrix(
    events: pd.DataFrame,
    feature_names: list[str],
    medians: Mapping[str, float] | None = None,
) -> tuple[pd.DataFrame, dict[str, float]]:
    values = events[feature_names].replace([np.inf, -np.inf], np.nan).astype(float)
    if medians is None:
        learned = {
            column: float(values[column].median())
            if np.isfinite(float(values[column].median()))
            else 0.0
            for column in feature_names
        }
    else:
        learned = {column: float(medians[column]) for column in feature_names}
    return values.fillna(learned).fillna(0.0), learned


def score_permutation_p(
    pool: pd.DataFrame,
    selected: pd.DataFrame,
    *,
    resamples: int,
    seed: int,
) -> float:
    """Randomly select the same number of pool outcomes under score independence."""

    n = len(selected)
    if n == 0 or n >= len(pool):
        return float("nan")
    observed = float(selected["net_return"].mean())
    returns = pool["net_return"].to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    exceed = 0
    for _ in range(resamples):
        draw = rng.choice(len(returns), size=n, replace=False)
        exceed += int(float(returns[draw].mean()) >= observed)
    return float((exceed + 1) / (resamples + 1))


def control_config(config: Mapping[str, Any]) -> dict[str, Any]:
    compatibility = deepcopy(load_v2_config())
    compatibility["matched_control"] = deepcopy(config["matched_control"])
    return compatibility


def fold_table(events: pd.DataFrame, labels: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "fold": label,
                **metrics(events[events["entry_time"].map(fold_label).eq(label)]),
            }
            for label in labels
        ]
    )


def corrected_failure_mechanics(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for event in events.to_dict("records"):
        net = float(event["net_return"])
        gross = float(event["gross_return"])
        mfe_exit = float(event["mfe_at_exit_atr"])
        mfe_horizon = float(event["horizon_mfe_atr"])
        outcome = str(event["outcome"])
        if net > 0.0:
            category = (
                "winner_large_giveback"
                if float(event["gave_back_atr"]) >= 2.0
                else "winner_retained"
            )
        elif gross > 0.0:
            category = "gross_win_erased_by_cost"
        elif "hard_stop" in outcome and mfe_exit < 0.5:
            category = (
                "early_stop_then_later_recovered"
                if mfe_horizon >= 2.0
                else "false_launch_early_stop"
            )
        elif mfe_exit >= 2.0:
            category = "armed_profit_given_back"
        elif "trend_ma" in outcome:
            category = "trend_ma_whipsaw_before_net_profit"
        elif outcome == "timeout":
            category = "timeout_negative"
        else:
            category = "other_loss"
        rows.append({**event, "failure_category": category})
    return pd.DataFrame(rows)


def selection_phase(config: dict[str, Any]) -> None:
    splits = config["splits"]
    train_start = utc(splits["train_start_inclusive"])
    train_end = utc(splits["train_end_exclusive"])
    tune_start = utc(splits["selection_start_inclusive"])
    tune_end = utc(splits["selection_end_exclusive"])
    base, quality = load_base_until(config, tune_end)
    v2 = load_v2_config()
    params = l1_params(config)
    train_frame, train_events = evaluate(base, v2, params, train_start, train_end)
    tune_frame, tune_events = evaluate(base, v2, params, tune_start, tune_end)
    cost = float(config["execution"]["round_trip_cost_fraction"])
    train = add_context_features(train_frame, train_events, cost=cost)
    tune = add_context_features(tune_frame, tune_events, cost=cost)
    feature_names = list(map(str, config["features"]["numeric"]))
    x_train, medians = matrix(train, feature_names)
    x_tune, _ = matrix(tune, feature_names, medians)
    lower = float(train["net_return"].quantile(0.01))
    upper = float(train["net_return"].quantile(0.99))
    target = train["net_return"].clip(lower, upper)
    model = lgb.LGBMRegressor(**model_params(config))
    model.fit(x_train, target)
    train["l2_score"] = model.predict(x_train)
    tune["l2_score"] = model.predict(x_tune)
    quantile = float(config["score_gate"]["selection_quantile"])
    score_threshold = float(tune["l2_score"].quantile(quantile))
    baseline_threshold = float(tune["signal_score"].quantile(quantile))
    selected = tune[tune["l2_score"].ge(score_threshold)].copy()
    baseline = tune[tune["signal_score"].ge(baseline_threshold)].copy()
    controls, pairs = matched_controls(
        selected,
        tune_frame,
        control_config(config),
        policy=params["runner_policy"],
        start=tune_start,
        end=tune_end,
    )
    matched = pairs[pairs["match_status"].eq("matched_exact")].copy()
    excess = matched["paired_excess_return"].astype(float)
    selected_metrics = metrics(selected)
    selected_metrics.update(
        {
            "score_permutation_p_one_sided": score_permutation_p(
                tune, selected, resamples=100_000, seed=20260904
            ),
            "matched_events": len(matched),
            "matched_control_excess_bp": float(excess.mean() * 1e4)
            if len(excess)
            else np.nan,
            "paired_control_signflip_p_one_sided": float(
                signflip_p(excess, resamples=100_000, seed=20260904)
            )
            if len(excess)
            else np.nan,
        }
    )
    RESULTS.mkdir(parents=True, exist_ok=True)
    model.booster_.save_model(str(MODEL_PATH))
    importance = pd.DataFrame(
        {
            "feature": feature_names,
            "split_importance": model.booster_.feature_importance(
                importance_type="split"
            ),
            "gain_importance": model.booster_.feature_importance(
                importance_type="gain"
            ),
        }
    ).sort_values(["gain_importance", "feature"], ascending=[False, True])
    write_csv(train, RESULTS / "train_scored_events.csv.gz")
    write_csv(tune, RESULTS / "selection_scored_events.csv.gz")
    write_csv(selected, RESULTS / "selection_l2_selected.csv.gz")
    write_csv(baseline, RESULTS / "selection_single_feature_baseline.csv.gz")
    write_csv(controls, RESULTS / "selection_controls.csv.gz")
    write_csv(pairs, RESULTS / "selection_control_pairs.csv")
    write_csv(importance, RESULTS / "feature_importance.csv")
    write_csv(fold_table(selected, ["2024H1", "2024H2"]), RESULTS / "selection_folds.csv")
    contract = {
        "feature_names": feature_names,
        "training_medians": medians,
        "target_clip_lower": lower,
        "target_clip_upper": upper,
        "score_quantile": quantile,
        "score_threshold": score_threshold,
        "single_feature_threshold": baseline_threshold,
        "model_params": model_params(config),
        "model_sha256": sha256_file(MODEL_PATH),
    }
    write_json(MODEL_CONTRACT_PATH, contract)
    receipt = {
        "phase": "selection_complete_validation_unopened",
        "config_sha256": sha256_file(CONFIG_PATH),
        "script_sha256": sha256_file(SCRIPT_PATH),
        "v2_config_sha256": sha256_file(V2_CONFIG_PATH),
        "model_sha256": sha256_file(MODEL_PATH),
        "model_contract_sha256": sha256_file(MODEL_CONTRACT_PATH),
        "source": quality,
        "holdout_rows_read": 0,
        "train_events": len(train),
        "selection_events": len(tune),
        "selection_rate": float(len(selected) / len(tune)),
        "selected_metrics": selected_metrics,
        "single_feature_baseline_metrics": metrics(baseline),
        "control_assignments": _assignment_metrics(controls),
        "selection_is_hypothesis_generation_not_validation": True,
    }
    write_json(SELECTION_RECEIPT, receipt)
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))


def assert_selection_committed(receipt: Mapping[str, Any]) -> None:
    paths = [
        CONFIG_PATH,
        SCRIPT_PATH,
        SELECTION_RECEIPT,
        MODEL_PATH,
        MODEL_CONTRACT_PATH,
    ]
    relative = [str(path.relative_to(ROOT)) for path in paths]
    subprocess.run(
        ["git", "ls-files", "--error-unmatch", *relative],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", *relative],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if dirty:
        raise RuntimeError(f"selection artifacts must be committed: {dirty}")
    expected = {
        "config_sha256": sha256_file(CONFIG_PATH),
        "script_sha256": sha256_file(SCRIPT_PATH),
        "v2_config_sha256": sha256_file(V2_CONFIG_PATH),
        "model_sha256": sha256_file(MODEL_PATH),
        "model_contract_sha256": sha256_file(MODEL_CONTRACT_PATH),
    }
    for key, value in expected.items():
        if receipt.get(key) != value:
            raise RuntimeError(f"selection {key} drift")


def validation_phase(config: dict[str, Any]) -> None:
    receipt = json.loads(SELECTION_RECEIPT.read_text(encoding="utf-8"))
    assert_selection_committed(receipt)
    contract = json.loads(MODEL_CONTRACT_PATH.read_text(encoding="utf-8"))
    splits = config["splits"]
    start = utc(splits["validation_start_inclusive"])
    end = utc(splits["validation_end_exclusive"])
    base, quality = load_base_until(config, end)
    params = l1_params(config)
    frame, events = evaluate(base, load_v2_config(), params, start, end)
    cost = float(config["execution"]["round_trip_cost_fraction"])
    pool = add_context_features(frame, events, cost=cost)
    feature_names = list(map(str, contract["feature_names"]))
    x_pool, _ = matrix(pool, feature_names, contract["training_medians"])
    booster = lgb.Booster(model_file=str(MODEL_PATH))
    pool["l2_score"] = booster.predict(x_pool)
    selected = pool[
        pool["l2_score"].ge(float(contract["score_threshold"]))
    ].copy()
    baseline = pool[
        pool["signal_score"].ge(float(contract["single_feature_threshold"]))
    ].copy()
    controls, pairs = matched_controls(
        selected,
        frame,
        control_config(config),
        policy=params["runner_policy"],
        start=start,
        end=end,
    )
    matched = pairs[pairs["match_status"].eq("matched_exact")].copy()
    excess = matched["paired_excess_return"].astype(float)
    assignments = _assignment_metrics(controls)
    result = metrics(selected)
    result.update(
        {
            "pool_events": len(pool),
            "selection_rate": float(len(selected) / len(pool)) if len(pool) else np.nan,
            "score_permutation_p_one_sided": score_permutation_p(
                pool, selected, resamples=100_000, seed=20260905
            ),
            "matched_events": len(matched),
            "matched_control_excess_bp": float(excess.mean() * 1e4)
            if len(excess)
            else np.nan,
            "paired_control_signflip_p_one_sided": float(
                signflip_p(excess, resamples=100_000, seed=20260905)
            )
            if len(excess)
            else np.nan,
        }
    )
    slices = fold_table(selected, list(map(str, splits["validation_slices"])))
    gate = config["validation_gate"]
    complete_slices_positive = all(
        int(row.events) < 12 or float(row.mean_net_bp) > 0.0
        for row in slices.itertuples(index=False)
    )
    gates = {
        "minimum_selected_events": len(selected) >= int(gate["minimum_selected_events"]),
        "mean_net_positive": float(result["mean_net_bp"]) > float(gate["mean_net_bp_gt"]),
        "score_permutation_p_lt_0_01": bool(
            np.isfinite(float(result["score_permutation_p_one_sided"]))
            and float(result["score_permutation_p_one_sided"])
            < float(gate["score_permutation_p_lt"])
        ),
        "paired_control_p_lt_0_01": bool(
            np.isfinite(float(result["paired_control_signflip_p_one_sided"]))
            and float(result["paired_control_signflip_p_one_sided"])
            < float(gate["paired_control_p_lt"])
        ),
        "all_eight_control_assignments_beaten": bool(
            len(assignments) == int(config["matched_control"]["controls_per_event"])
            and all(
                float(result["mean_net_bp"]) > float(row["mean_net_bp"])
                for row in assignments
            )
        ),
        "complete_slices_positive": complete_slices_positive,
    }
    gates["all_pass"] = all(gates.values())
    failures = corrected_failure_mechanics(selected)
    write_csv(pool, RESULTS / "validation_scored_pool.csv.gz")
    write_csv(selected, RESULTS / "validation_l2_selected.csv.gz")
    write_csv(baseline, RESULTS / "validation_single_feature_baseline.csv.gz")
    write_csv(controls, RESULTS / "validation_controls.csv.gz")
    write_csv(pairs, RESULTS / "validation_control_pairs.csv")
    write_csv(slices, RESULTS / "validation_slices.csv")
    write_csv(failures, RESULTS / "validation_failure_mechanics.csv.gz")
    write_json(
        RESULTS / "validation_summary.json",
        {
            "phase": "frozen_preholdout_validation_complete",
            "metrics": result,
            "pool_metrics": metrics(pool),
            "single_feature_baseline_metrics": metrics(baseline),
            "control_assignments": assignments,
            "gates": gates,
            "source": quality,
            "holdout_rows_read": 0,
            "production_eligible": False,
        },
    )
    print(json.dumps(json_value({"metrics": result, "gates": gates}), ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True, choices=("selection", "validation"))
    args = parser.parse_args()
    config = load_config()
    if args.phase == "selection":
        selection_phase(config)
    else:
        validation_phase(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
