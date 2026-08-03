"""P2-L2 research training and pre-holdout economic validation.

The module consumes only the explicit immutable P1 short-L2 dataframe.  Model
features are the manifest's 28 causal columns at ``signal_time``; the regression
target is ``net_ret_swap_taker``, which already includes the 10 bp round-trip
taker fee.  The Owner-approved pressure view subtracts exactly one additional
5 bp slippage allowance and never deducts the full 15 bp a second time.

All splits are chronological.  Each boundary purges complete label-interval
event components.  Runtime threshold calibration sees scores only, never
returns.  Exact top-decile diagnostics use equal fractional weight for every
row in a boundary tie, so they have exactly 10% total weight without selecting
arbitrary tied candidate IDs.  The deployable fixed gate instead admits the
whole equality block and must pass its pre-registered health limits.
"""
from __future__ import annotations

import hashlib
import itertools
import math
from dataclasses import dataclass
from typing import Any, Sequence

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.linear_model import LinearRegression
from sklearn.metrics import average_precision_score, roc_auc_score

from src.judgment.features import FEATURE_COLUMNS
from src.judgment.p2_protocol import (
    ACTUAL_COST_PRESSURE_TOTAL,
    ADDITIONAL_SLIPPAGE_ROUND_TRIP,
    HOLDOUT_CUTOFF,
    WALKFORWARD_BOUNDARIES,
    P2ProtocolError,
    apply_runtime_gate,
    calibrate_runtime_gate,
    prepare_split_at_boundaries,
)

TARGET_COLUMN = "net_ret_swap_taker"
LABEL_COLUMN = "label_tp_before_sl"
SEED = 42
MODEL_PARAMS: dict[str, Any] = {
    "objective": "regression",
    "metric": "None",
    "learning_rate": 0.05,
    "num_leaves": 15,
    "min_child_samples": 30,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "seed": SEED,
    "feature_fraction_seed": SEED,
    "bagging_seed": SEED,
    "data_random_seed": SEED,
    "deterministic": True,
    "force_col_wise": True,
    "num_threads": 1,
    "verbosity": -1,
}


@dataclass(frozen=True)
class WalkforwardFold:
    """One expanding fold with three prior segments and one untouched test."""

    fold: int
    train: pd.DataFrame
    early_stop: pd.DataFrame
    calibration: pd.DataFrame
    test: pd.DataFrame
    purged: pd.DataFrame
    early_stop_start: pd.Timestamp
    calibration_start: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


def exact_top_fraction_weights(scores: Sequence[float], fraction: float = 0.10) -> dict[str, Any]:
    """Return exact-fraction weights without choosing among equal scores."""
    values = np.asarray(scores, dtype=float)
    if values.ndim != 1 or not len(values) or not np.isfinite(values).all():
        raise P2ProtocolError("top-fraction scores must be a non-empty finite vector")
    if not 0.0 < fraction < 1.0:
        raise P2ProtocolError("top fraction must be between zero and one")
    target_n = max(1, int(math.floor(len(values) * fraction)))
    boundary = float(np.sort(values)[::-1][target_n - 1])
    above = values > boundary
    equal = values == boundary
    remaining = target_n - int(above.sum())
    if remaining <= 0 or not equal.any():
        raise P2ProtocolError("invalid top-fraction boundary accounting")
    equality_weight = float(remaining / int(equal.sum()))
    weights = np.zeros(len(values), dtype=float)
    weights[above] = 1.0
    weights[equal] = equality_weight
    if not np.isclose(weights.sum(), target_n, atol=1e-12, rtol=0):
        raise P2ProtocolError("top-fraction weights do not sum to target")
    return {
        "weights": weights,
        "target_n": int(target_n),
        "boundary": boundary,
        "n_strictly_above": int(above.sum()),
        "n_equal_boundary": int(equal.sum()),
        "equality_weight": equality_weight,
        "boundary_tied": bool(int(equal.sum()) > remaining),
    }


def _weighted_mean(values: np.ndarray, weights: np.ndarray) -> float:
    return float(np.sum(values * weights) / np.sum(weights))


def _profit_factor(values: np.ndarray, weights: np.ndarray | None = None) -> float | None:
    use_weights = np.ones(len(values), dtype=float) if weights is None else weights
    positive = float(np.sum(values[values > 0] * use_weights[values > 0]))
    negative = float(np.sum(values[values < 0] * use_weights[values < 0]))
    return positive / -negative if negative < 0 else None


def economic_metrics(
    frame: pd.DataFrame,
    scores: Sequence[float],
    *,
    selected_mask: Sequence[bool] | None = None,
    exact_top_fraction: bool = False,
) -> dict[str, Any]:
    """Economic metrics with one and only one approved slippage deduction."""
    values = np.asarray(scores, dtype=float)
    if len(frame) != len(values):
        raise P2ProtocolError("frame and score lengths differ")
    if selected_mask is not None and exact_top_fraction:
        raise P2ProtocolError("choose fixed mask or exact top fraction, not both")
    if exact_top_fraction:
        top = exact_top_fraction_weights(values)
        weights = np.asarray(top.pop("weights"), dtype=float)
        selected_n = int(top["target_n"])
        selection = {"mode": "exact_top_decile_fractional_ties", **top}
    else:
        if selected_mask is None:
            mask = np.ones(len(frame), dtype=bool)
            mode = "all"
        else:
            mask = np.asarray(selected_mask, dtype=bool)
            if len(mask) != len(frame):
                raise P2ProtocolError("selection mask length differs from frame")
            mode = "fixed_runtime_gate"
        weights = mask.astype(float)
        selected_n = int(mask.sum())
        selection = {"mode": mode}
    if selected_n == 0 or weights.sum() <= 0:
        return {"n": 0, "selection": selection}

    gross = frame["gross_ret"].to_numpy(dtype=float)
    net_taker = frame[TARGET_COLUMN].to_numpy(dtype=float)
    pressure_net = net_taker - ADDITIONAL_SLIPPAGE_ROUND_TRIP
    labels = frame[LABEL_COLUMN].to_numpy(dtype=float)
    return {
        "n": selected_n,
        "effective_n": float(weights.sum()),
        "pass_rate": float(weights.sum() / len(frame)),
        "mean_gross_ret": _weighted_mean(gross, weights),
        "mean_net_taker": _weighted_mean(net_taker, weights),
        "mean_pressure_net": _weighted_mean(pressure_net, weights),
        "pressure_profit_factor": _profit_factor(pressure_net, weights),
        "win_rate_tp_before_sl": _weighted_mean(labels, weights),
        "approved_total_cost": ACTUAL_COST_PRESSURE_TOTAL,
        "additional_slippage_deducted": ADDITIONAL_SLIPPAGE_ROUND_TRIP,
        "selection": selection,
    }


def rank_metrics(frame: pd.DataFrame, scores: Sequence[float]) -> dict[str, Any]:
    values = np.asarray(scores, dtype=float)
    labels = frame[LABEL_COLUMN].to_numpy(dtype=int)
    returns = frame[TARGET_COLUMN].to_numpy(dtype=float)
    rho = spearmanr(values, returns).statistic
    return {
        "roc_auc": float(roc_auc_score(labels, values)),
        "pr_auc": float(average_precision_score(labels, values)),
        "spearman_score_vs_net_taker": None if np.isnan(rho) else float(rho),
    }


def _top_decile_eval(predictions: np.ndarray, dataset: lgb.Dataset) -> tuple[str, float, bool]:
    labels = np.asarray(dataset.get_label(), dtype=float)
    top = exact_top_fraction_weights(predictions)
    score = _weighted_mean(labels, np.asarray(top["weights"], dtype=float))
    return "exact_top_decile_net_taker", score, True


def train_regressor(
    train: pd.DataFrame,
    early_stop: pd.DataFrame,
    *,
    feature_columns: Sequence[str] = FEATURE_COLUMNS,
    num_boost_round: int = 600,
    early_stopping_rounds: int = 50,
) -> lgb.Booster:
    """Fit the single pre-registered LightGBM configuration."""
    columns = list(feature_columns)
    missing = [column for column in (*columns, TARGET_COLUMN) if column not in train]
    if missing:
        raise P2ProtocolError(f"training frame missing columns: {missing}")
    if list(columns) != list(FEATURE_COLUMNS):
        raise P2ProtocolError("P2 full model must use the exact manifest 28-feature order")
    dtrain = lgb.Dataset(train[columns], label=train[TARGET_COLUMN].to_numpy(dtype=float))
    dval = lgb.Dataset(
        early_stop[columns],
        label=early_stop[TARGET_COLUMN].to_numpy(dtype=float),
        reference=dtrain,
    )
    return lgb.train(
        dict(MODEL_PARAMS),
        dtrain,
        num_boost_round=num_boost_round,
        valid_sets=[dval],
        valid_names=["early_stop"],
        feval=_top_decile_eval,
        callbacks=[
            lgb.early_stopping(
                early_stopping_rounds,
                first_metric_only=True,
                verbose=False,
            )
        ],
    )


def predict(model: lgb.Booster, frame: pd.DataFrame) -> np.ndarray:
    return np.asarray(
        model.predict(frame[list(FEATURE_COLUMNS)], num_iteration=model.best_iteration),
        dtype=float,
    )


def fit_single_feature_baseline(train: pd.DataFrame) -> LinearRegression:
    model = LinearRegression()
    model.fit(train[["ma_spread_pct"]], train[TARGET_COLUMN].to_numpy(dtype=float))
    return model


def predict_single_feature(model: LinearRegression, frame: pd.DataFrame) -> np.ndarray:
    return np.asarray(model.predict(frame[["ma_spread_pct"]]), dtype=float)


def _fraction_boundary(times: pd.Series, fraction: float) -> pd.Timestamp:
    ordered = pd.Series(pd.to_datetime(times, utc=True)).sort_values().reset_index(drop=True)
    if ordered.empty:
        raise P2ProtocolError("cannot choose a split boundary from an empty series")
    index = min(len(ordered) - 1, max(1, int(math.floor(len(ordered) * fraction))))
    return pd.Timestamp(ordered.iloc[index])


def prepare_walkforward_fold(
    frame: pd.DataFrame,
    *,
    fold: int,
    test_start: pd.Timestamp,
    test_end: pd.Timestamp,
) -> WalkforwardFold:
    """Build one expanding chronological fold with four dependency-safe parts."""
    data = frame.copy()
    for column in ("signal_time", "interval_start", "interval_end"):
        data[column] = pd.to_datetime(data[column], utc=True, errors="raise")
    test_start = pd.Timestamp(test_start).tz_convert("UTC")
    test_end = pd.Timestamp(test_end).tz_convert("UTC")
    prior_times = data.loc[data["signal_time"] < test_start, "signal_time"]
    early_start = _fraction_boundary(prior_times, 0.70)
    calibration_start = _fraction_boundary(prior_times, 0.85)
    inner = prepare_split_at_boundaries(
        data,
        early_stop_start=early_start,
        calibration_start=calibration_start,
        final_cutoff=test_start,
    )

    eligible_test = (data["signal_time"] >= test_start) & (data["signal_time"] < test_end)
    valid_test = eligible_test & (data["interval_end"] < test_end)
    boundary_groups = set(
        data.loc[eligible_test & ~valid_test, "event_group_id"].astype(str)
    )
    test = data.loc[valid_test].copy()
    if boundary_groups:
        test = test.loc[~test["event_group_id"].astype(str).isin(boundary_groups)]
    inner_purged_groups = set(inner.purged["event_group_id"].astype(str))
    tainted_test_groups = inner_purged_groups & set(test["event_group_id"].astype(str))
    if tainted_test_groups:
        test = test.loc[~test["event_group_id"].astype(str).isin(tainted_test_groups)]
    prior_groups = set(
        pd.concat([inner.train, inner.early_stop, inner.calibration], ignore_index=True)[
            "event_group_id"
        ].astype(str)
    )
    test_shared = prior_groups & set(test["event_group_id"].astype(str))
    if test_shared:
        test = test.loc[~test["event_group_id"].astype(str).isin(test_shared)]
    kept_test_ids = set(test["candidate_id"].astype(str))
    test = test.sort_values(["signal_time", "event_group_id"]).reset_index(drop=True)
    if test.empty:
        raise P2ProtocolError(f"walkforward fold {fold} has no test rows")
    purged_test = data.loc[
        eligible_test & ~data["candidate_id"].astype(str).isin(kept_test_ids)
    ].copy()
    purged = pd.concat([inner.purged, purged_test], ignore_index=True).drop_duplicates(
        subset=["candidate_id"]
    )
    return WalkforwardFold(
        fold=fold,
        train=inner.train,
        early_stop=inner.early_stop,
        calibration=inner.calibration,
        test=test,
        purged=purged,
        early_stop_start=early_start,
        calibration_start=calibration_start,
        test_start=test_start,
        test_end=test_end,
    )


def walkforward_folds(frame: pd.DataFrame) -> list[WalkforwardFold]:
    boundaries = list(WALKFORWARD_BOUNDARIES)
    folds: list[WalkforwardFold] = []
    for index, start in enumerate(boundaries):
        end = boundaries[index + 1] if index + 1 < len(boundaries) else HOLDOUT_CUTOFF
        folds.append(
            prepare_walkforward_fold(
                frame,
                fold=index + 1,
                test_start=start,
                test_end=end,
            )
        )
    return folds


def _stable_order(value: object, seed: int) -> str:
    return hashlib.sha256(f"{seed}\0{value}".encode("utf-8")).hexdigest()


def matched_candidate_pairs(
    frame: pd.DataFrame,
    selected_mask: Sequence[bool],
    *,
    seed: int = SEED,
) -> pd.DataFrame:
    """Match selected rows to P1 nonselected candidates without replacement.

    Matching columns are exactly the pre-registered symbol, ISO UTC week, and
    fold-local ATR quintile.  Outcome values never participate in pairing.
    """
    data = frame.copy().reset_index(drop=True)
    selected = np.asarray(selected_mask, dtype=bool)
    if len(data) != len(selected):
        raise P2ProtocolError("matched-control mask length differs from frame")
    times = pd.to_datetime(data["signal_time"], utc=True)
    iso = times.dt.isocalendar()
    data["_week"] = iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)
    atr = data["atr_pct"].to_numpy(dtype=float)
    edges = np.quantile(atr, [0.2, 0.4, 0.6, 0.8])
    data["_atr_bucket"] = np.searchsorted(edges, atr, side="right").astype(int)
    data["_selected"] = selected
    selected_groups = set(data.loc[selected, "event_group_id"].astype(str))
    controls = data.loc[
        ~selected & ~data["event_group_id"].astype(str).isin(selected_groups)
    ].copy()
    controls["_order"] = controls["candidate_id"].map(lambda value: _stable_order(value, seed))
    control_pools: dict[tuple[str, str, int], list[int]] = {}
    for key, part in controls.groupby(["symbol", "_week", "_atr_bucket"], sort=True):
        control_pools[(str(key[0]), str(key[1]), int(key[2]))] = (
            part.sort_values("_order").index.tolist()
        )

    chosen: list[dict[str, Any]] = []
    selected_rows = data.loc[selected].copy()
    selected_rows["_order"] = selected_rows["candidate_id"].map(
        lambda value: _stable_order(value, seed)
    )
    for selected_index, row in selected_rows.sort_values("_order").iterrows():
        key = (str(row["symbol"]), str(row["_week"]), int(row["_atr_bucket"]))
        pool = control_pools.get(key, [])
        if not pool:
            continue
        control_index = pool.pop(0)
        control = data.loc[control_index]
        chosen.append(
            {
                "selected_candidate_id": str(row["candidate_id"]),
                "control_candidate_id": str(control["candidate_id"]),
                "symbol": str(row["symbol"]),
                "utc_week": str(row["_week"]),
                "atr_bucket": int(row["_atr_bucket"]),
                "selected_event_group_id": str(row["event_group_id"]),
                "control_event_group_id": str(control["event_group_id"]),
                "selected_net_taker": float(row[TARGET_COLUMN]),
                "control_net_taker": float(control[TARGET_COLUMN]),
                "selected_pressure_net": float(row[TARGET_COLUMN])
                - ADDITIONAL_SLIPPAGE_ROUND_TRIP,
                "control_pressure_net": float(control[TARGET_COLUMN])
                - ADDITIONAL_SLIPPAGE_ROUND_TRIP,
            }
        )
    return pd.DataFrame(chosen)


def exact_block_signflip_pvalue(pairs: pd.DataFrame) -> dict[str, Any]:
    """One-sided exact UTC-week block sign-flip test of matched economic lift."""
    if pairs.empty:
        return {
            "status": "unavailable_no_pairs",
            "n_pairs": 0,
            "n_blocks": 0,
            "observed_lift": None,
            "p_value": None,
        }
    delta = pairs["selected_pressure_net"] - pairs["control_pressure_net"]
    block_sums = delta.groupby(pairs["utc_week"]).sum().sort_index()
    n_blocks = int(len(block_sums))
    if n_blocks > 20:
        raise P2ProtocolError("exact block sign-flip exceeds 20 blocks")
    observed = float(delta.mean())
    sums = block_sums.to_numpy(dtype=float)
    total_n = len(delta)
    hits = 0
    permutations = 2 ** n_blocks
    for signs in itertools.product((-1.0, 1.0), repeat=n_blocks):
        statistic = float(np.dot(np.asarray(signs), sums) / total_n)
        if statistic >= observed - 1e-15:
            hits += 1
    return {
        "status": "ok",
        "n_pairs": int(len(pairs)),
        "n_blocks": n_blocks,
        "blocks": block_sums.index.tolist(),
        "observed_lift": observed,
        "permutations": int(permutations),
        "hits_ge_observed": int(hits),
        "p_value": float(hits / permutations),
    }


def evaluate_fixed_gate(
    frame: pd.DataFrame,
    scores: np.ndarray,
    gate: dict[str, Any],
) -> tuple[dict[str, Any], np.ndarray]:
    mask = apply_runtime_gate(scores, threshold=float(gate["threshold"]))
    metrics = {
        "rank": rank_metrics(frame, scores),
        "exact_top_decile": economic_metrics(frame, scores, exact_top_fraction=True),
        "fixed_gate": economic_metrics(frame, scores, selected_mask=mask),
        "fixed_gate_threshold": float(gate["threshold"]),
        "fixed_gate_operator": str(gate["threshold_operator"]),
        "score_distinct_count": int(np.unique(scores).size),
    }
    return metrics, mask
