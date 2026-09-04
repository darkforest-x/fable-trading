#!/usr/bin/env python3
"""Diagnose whether BTCUSDT.P 15m trend-runner trades are causally isolatable.

Every ex-ante feature is sampled at the completed signal bar ``t`` or earlier.
The aeon sequence tensor contains 64 completed 15m bars ending at ``t``.  The
early-management probes are a separate decision surface: their features end at
the declared checkpoint close, and an exit decided there fills only at the next
bar open.  Only ``runner_armed`` and realized trade outcomes read future bars.

The source ledgers end on 2026-02-28, before the repository holdout beginning
2026-05-04.  This experiment is retrospective because all three periods were
already inspected by the parent experiment; it is not a fresh confirmation.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from scripts.backtest_two_key_candle_pine_v8_btc_1h import signflip_p
from scripts.research_btcusdtp_15m_high_recall_l2_runner import (
    load_base_until,
    load_config as load_market_config,
    score_permutation_p,
)
from scripts.research_btcusdtp_15m_multifactor_confluence import (
    _matched_control_metrics,
)
from scripts.research_btcusdtp_15m_ma_state_trend import (
    json_value,
    metrics,
    utc,
    write_csv,
    write_json,
)
from scripts.research_two_key_candle_ma_retest_1h import sha256_file


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-btcusdtp-15m-runner-isolation-preholdout-20260904-v1"
EXPERIMENT = ROOT / "experiments" / "active" / EXPERIMENT_ID
CONFIG_PATH = EXPERIMENT / "config.json"
RESULTS = EXPERIMENT / "results"
SCRIPT_PATH = Path(__file__).resolve()
BAR_DELTA = pd.Timedelta(minutes=15)
EARLY_COLUMNS = [
    "early_close_profit_atr",
    "early_max_close_profit_atr",
    "early_min_close_profit_atr",
    "early_mfe_atr",
    "early_mae_atr",
    "early_mean_close_profit_atr",
    "early_path_slope_atr",
    "early_positive_close_share",
    "early_up_step_share",
    "early_range_mean_atr",
    "early_range_max_atr",
    "early_volume_mean_ratio20",
    "early_volume_max_ratio20",
    "early_ema30_distance_atr",
    "early_sma60_distance_atr",
    "early_ema30_correct_side_share",
    "early_sma60_correct_side_share",
]


def load_config() -> dict[str, Any]:
    """Load the registered retrospective experiment contract."""

    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _verify_path(record: Mapping[str, Any]) -> Path:
    path = ROOT / str(record["path"])
    actual = sha256_file(path)
    if actual != str(record["sha256"]):
        raise RuntimeError(f"source hash drift: {path}: {actual}")
    return path


def verify_sources(config: Mapping[str, Any]) -> None:
    """Fail closed when any parent ledger or source contract has drifted."""

    _verify_path(config["lineage"]["parent_config"])
    _verify_path(config["lineage"]["parent_runner"])
    for record in config["sources"]["feature_ledgers"].values():
        _verify_path(record)
    _verify_path(config["sources"]["btc_5m"])


def parent_feature_names(config: Mapping[str, Any]) -> list[str]:
    """Return the parent's 103 registered, signal-time causal features."""

    path = ROOT / str(config["lineage"]["parent_config"]["path"])
    parent = json.loads(path.read_text(encoding="utf-8"))
    return list(
        dict.fromkeys(
            column
            for columns in parent["feature_contract"]["feature_groups"].values()
            for column in columns
        )
    )


def load_ledgers(config: Mapping[str, Any]) -> dict[str, pd.DataFrame]:
    """Load fixed parent ledgers and enforce their time/holdout boundaries."""

    output: dict[str, pd.DataFrame] = {}
    mapping = {
        "development": "development",
        "confirmation": "confirmation",
        "audit": "diagnostic_audit",
        "audit_selected": "diagnostic_audit",
    }
    holdout = utc(config["sources"]["holdout_start"])
    for name, period_name in mapping.items():
        path = ROOT / str(config["sources"]["feature_ledgers"][name]["path"])
        frame = pd.read_csv(path, parse_dates=["signal_time", "entry_time", "exit_time"])
        frame["signal_time"] = pd.to_datetime(frame["signal_time"], utc=True)
        frame["entry_time"] = pd.to_datetime(frame["entry_time"], utc=True)
        frame["exit_time"] = pd.to_datetime(frame["exit_time"], utc=True)
        start, end = map(utc, config["periods"][period_name])
        if not frame["entry_time"].ge(start).all() or not frame["entry_time"].lt(end).all():
            raise RuntimeError(f"{name} ledger escaped registered period")
        if frame["signal_time"].ge(holdout).any():
            raise RuntimeError(f"{name} materialized repository holdout")
        if frame["setup_id"].duplicated().any():
            raise RuntimeError(f"{name} contains duplicate setup_id")
        output[name] = frame.reset_index(drop=True)
    return output


def load_market(config: Mapping[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load complete 15m market rows through the diagnostic audit only."""

    end = utc(config["periods"]["diagnostic_audit"][1])
    frame, receipt = load_base_until(load_market_config(), end)
    pieces: list[pd.DataFrame] = []
    for _, segment in frame.groupby("segment_id", sort=False):
        current = segment.copy()
        hl2 = (current["high"] + current["low"]) / 2.0
        current["ema30"] = hl2.ewm(span=30, adjust=False, min_periods=30).mean()
        current["volume_median20_prior"] = (
            current["volume"].shift(1).rolling(20, min_periods=12).median()
        )
        pieces.append(current)
    output = pd.concat(pieces).sort_index()
    output["trend_ma"] = output["sma60"]
    if utc(receipt["last_time"]) >= utc(config["sources"]["holdout_start"]):
        raise RuntimeError("market loader crossed repository holdout")
    return output, receipt


def _matrix(
    train: pd.DataFrame,
    test: pd.DataFrame,
    columns: Sequence[str],
    medians: Mapping[str, float] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float]]:
    left = train.loc[:, columns].replace([np.inf, -np.inf], np.nan).astype(float)
    right = test.loc[:, columns].replace([np.inf, -np.inf], np.nan).astype(float)
    if medians is None:
        learned = {
            column: float(value) if np.isfinite(float(value)) else 0.0
            for column, value in left.median().items()
        }
    else:
        learned = {column: float(medians[column]) for column in columns}
    return left.fillna(learned).fillna(0.0), right.fillna(learned).fillna(0.0), learned


def _lgb_params(config: Mapping[str, Any]) -> dict[str, Any]:
    row = config["ex_ante_screen"]["lightgbm"]
    seed = int(row["seed"])
    return {
        "n_estimators": int(row["n_estimators"]),
        "learning_rate": float(row["learning_rate"]),
        "num_leaves": int(row["num_leaves"]),
        "max_depth": int(row["max_depth"]),
        "min_child_samples": int(row["min_child_samples"]),
        "reg_lambda": float(row["reg_lambda"]),
        "colsample_bytree": float(row["colsample_bytree"]),
        "subsample": float(row["subsample"]),
        "verbosity": -1,
        "deterministic": True,
        "force_col_wise": True,
        "n_jobs": 1,
        "random_state": seed,
        "data_random_seed": seed,
        "feature_fraction_seed": seed,
        "bagging_seed": seed,
        "extra_seed": seed,
    }


def strict_k1k2_score(events: pd.DataFrame) -> np.ndarray:
    """Pine-compatible strict episode gate; higher values remain better."""

    strict = (
        events["k1_found"].eq(1.0)
        & events["k1_gap_bars"].between(2.0, 8.0)
        & events["between_wrong_side_share"].le(0.0)
        & events["k2_touch_depth_atr"].ge(0.0)
        & events["k2_body_clearance_atr"].ge(0.0)
    )
    tie_break = events["signal_score"].fillna(0.0).to_numpy(dtype=float) * 1e-6
    return strict.astype(float).to_numpy() + tie_break


def sequence_tensor(
    market: pd.DataFrame,
    events: pd.DataFrame,
    *,
    window: int,
) -> np.ndarray:
    """Build six causal channels over bars ``t-window+1..t``.

    Reads open/high/low/close/volume, ATR14, EMA30(HL2), SMA60(HL2), and
    prior-20-bar median volume.  Directional channels are mirrored so long and
    short launches share the same orientation.  A source-gap prefix is padded
    with zeros rather than reading another segment.
    """

    atr = market["atr"].astype(float).replace(0.0, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        channels = np.vstack(
            [
                market["close"].astype(float).diff().to_numpy() / atr.to_numpy(),
                (market["close"] - market["open"]).to_numpy(dtype=float)
                / atr.to_numpy(),
                (market["close"] - market["ema30"]).to_numpy(dtype=float)
                / atr.to_numpy(),
                (market["ema30"] - market["ema30"].shift(4)).to_numpy(dtype=float)
                / (4.0 * atr.to_numpy()),
                (market["high"] - market["low"]).to_numpy(dtype=float)
                / atr.to_numpy(),
                np.log(
                    np.maximum(
                        market["volume"].to_numpy(dtype=float)
                        / market["volume_median20_prior"].to_numpy(dtype=float),
                        1e-6,
                    )
                ),
            ]
        )
    segments = market["segment_id"].to_numpy(dtype=int)
    starts = {
        int(segment_id): int(group.index.min())
        for segment_id, group in market.groupby("segment_id", sort=False)
    }
    tensor = np.zeros((len(events), channels.shape[0], window), dtype=np.float32)
    for position, event in enumerate(events.itertuples(index=False)):
        signal_i = int(event.signal_i)
        start = max(starts[int(segments[signal_i])], signal_i - window + 1)
        values = channels[:, start : signal_i + 1].copy()
        values[:4] *= int(event.direction)
        values = np.clip(
            np.nan_to_num(values, nan=0.0, posinf=5.0, neginf=-5.0), -5.0, 5.0
        )
        tensor[position, :, -values.shape[1] :] = values
    return tensor


def _binary_auc(labels: Iterable[bool], scores: Iterable[float]) -> float:
    y = np.asarray(list(labels), dtype=int)
    s = np.asarray(list(scores), dtype=float)
    return float(roc_auc_score(y, s)) if len(np.unique(y)) == 2 else np.nan


def _average_precision(labels: Iterable[bool], scores: Iterable[float]) -> float:
    y = np.asarray(list(labels), dtype=int)
    s = np.asarray(list(scores), dtype=float)
    return float(average_precision_score(y, s)) if len(np.unique(y)) == 2 else np.nan


def _best_single_contract(
    train: pd.DataFrame, feature_names: Sequence[str]
) -> tuple[dict[str, Any], np.ndarray]:
    labels = train["runner_armed"].astype(int).to_numpy()
    best: tuple[float, str, int, float, np.ndarray] | None = None
    for column in feature_names:
        values = train[column].replace([np.inf, -np.inf], np.nan).astype(float)
        median = float(values.median())
        median = median if np.isfinite(median) else 0.0
        array = values.fillna(median).to_numpy(dtype=float)
        auc = _binary_auc(labels, array)
        direction = 1 if auc >= 0.5 else -1
        oriented = direction * array
        candidate = (abs(auc - 0.5), column, direction, median, oriented)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    if best is None:
        raise RuntimeError("no single feature candidate")
    _, column, direction, median, scores = best
    return {
        "kind": "single_feature",
        "feature": column,
        "direction": direction,
        "median": median,
    }, scores


def _fit_lgb_classifier(
    train: pd.DataFrame,
    feature_names: Sequence[str],
    label: pd.Series,
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], np.ndarray]:
    x, _, medians = _matrix(train, train, feature_names)
    params = _lgb_params(config)
    model = lgb.LGBMClassifier(
        objective="binary",
        class_weight=str(config["ex_ante_screen"]["lightgbm"]["class_weight"]),
        **params,
    )
    model.fit(x, label.astype(int).to_numpy())
    return {
        "kind": "lgb_classifier",
        "features": list(feature_names),
        "medians": medians,
        "model": model,
    }, model.predict_proba(x)[:, 1]


def _fit_two_stage(
    train: pd.DataFrame,
    feature_names: Sequence[str],
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], np.ndarray]:
    arm_contract, arm_scores = _fit_lgb_classifier(
        train, feature_names, train["runner_armed"], config
    )
    x, _, medians = _matrix(train, train, feature_names)
    models: dict[str, Any] = {}
    clips: dict[str, tuple[float, float]] = {}
    params = _lgb_params(config)
    for state, mask in {
        "armed": train["runner_armed"].astype(bool),
        "unarmed": ~train["runner_armed"].astype(bool),
    }.items():
        target = train.loc[mask, "net_return"].astype(float) * 1e4
        low, high = map(float, target.quantile([0.01, 0.99]).to_numpy())
        model = lgb.LGBMRegressor(objective="huber", alpha=0.8, **params)
        model.fit(x.loc[mask], target.clip(low, high))
        models[state] = model
        clips[state] = (low, high)
    armed_net = models["armed"].predict(x)
    unarmed_net = models["unarmed"].predict(x)
    scores = arm_scores * armed_net + (1.0 - arm_scores) * unarmed_net
    return {
        "kind": "two_stage",
        "features": list(feature_names),
        "medians": medians,
        "arm_contract": arm_contract,
        "models": models,
        "target_clips_bp": clips,
    }, np.asarray(scores, dtype=float)


def _fit_minirocket(
    tensor: np.ndarray,
    labels: pd.Series,
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], np.ndarray]:
    try:
        from aeon.classification.convolution_based import MiniRocketClassifier
    except ImportError as exc:  # pragma: no cover - only isolated evaluator runs this arm
        raise RuntimeError("aeon evaluator is required for the registered sequence arm") from exc
    from sklearn.linear_model import LogisticRegression

    row = config["ex_ante_screen"]["aeon"]
    estimator = LogisticRegression(
        C=0.05,
        class_weight="balanced",
        max_iter=2000,
        solver="liblinear",
        random_state=int(row["seed"]),
    )
    model = MiniRocketClassifier(
        n_kernels=int(row["n_kernels"]),
        estimator=estimator,
        n_jobs=4,
        random_state=int(row["seed"]),
    )
    model.fit(tensor, labels.astype(int).to_numpy())
    return {"kind": "minirocket", "model": model}, model.predict_proba(tensor)[:, 1]


def fit_variant(
    variant: str,
    train: pd.DataFrame,
    train_tensor: np.ndarray,
    feature_names: Sequence[str],
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], np.ndarray]:
    if variant == "signal_score":
        return {"kind": "column", "column": "signal_score"}, train[
            "signal_score"
        ].to_numpy(dtype=float)
    if variant == "strict_k1k2_rule":
        return {"kind": "strict"}, strict_k1k2_score(train)
    if variant == "best_single_feature_arm":
        return _best_single_contract(train, feature_names)
    if variant == "lgb_arm_all103":
        return _fit_lgb_classifier(
            train, feature_names, train["runner_armed"], config
        )
    if variant == "lgb_profitable_runner_all103":
        label = train["runner_armed"].astype(bool) & train["net_return"].gt(0.0)
        return _fit_lgb_classifier(train, feature_names, label, config)
    if variant == "lgb_two_stage_expected_net":
        return _fit_two_stage(train, feature_names, config)
    if variant == "aeon_minirocket_arm_seq64":
        return _fit_minirocket(train_tensor, train["runner_armed"], config)
    raise KeyError(variant)


def predict_variant(
    contract: Mapping[str, Any],
    train: pd.DataFrame,
    test: pd.DataFrame,
    test_tensor: np.ndarray,
) -> np.ndarray:
    kind = str(contract["kind"])
    if kind == "column":
        return test[str(contract["column"])].to_numpy(dtype=float)
    if kind == "strict":
        return strict_k1k2_score(test)
    if kind == "single_feature":
        values = (
            test[str(contract["feature"])]
            .replace([np.inf, -np.inf], np.nan)
            .fillna(float(contract["median"]))
            .to_numpy(dtype=float)
        )
        return int(contract["direction"]) * values
    if kind == "minirocket":
        return contract["model"].predict_proba(test_tensor)[:, 1]
    features = list(contract["features"])
    _, x, _ = _matrix(train, test, features, contract["medians"])
    if kind == "lgb_classifier":
        return contract["model"].predict_proba(x)[:, 1]
    if kind == "two_stage":
        arm = contract["arm_contract"]["model"].predict_proba(x)[:, 1]
        armed_net = contract["models"]["armed"].predict(x)
        unarmed_net = contract["models"]["unarmed"].predict(x)
        return arm * armed_net + (1.0 - arm) * unarmed_net
    raise KeyError(kind)


def threshold_for(
    variant: str, train_scores: np.ndarray, quantile: float
) -> float:
    if variant == "strict_k1k2_rule":
        return 0.5
    return float(np.nanquantile(train_scores, quantile))


def evaluate_selection(
    pool: pd.DataFrame,
    scores: np.ndarray,
    threshold: float,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    scored = pool.copy()
    scored["model_score"] = scores
    scored["score_threshold"] = threshold
    scored["selected"] = np.isfinite(scores) & (scores >= threshold)
    selected = scored.loc[scored["selected"]].copy()
    arm_rate = float(pool["runner_armed"].astype(bool).mean())
    armed = int(pool["runner_armed"].astype(bool).sum())
    selected_armed = int(selected["runner_armed"].astype(bool).sum())
    precision = (
        float(selected["runner_armed"].astype(bool).mean()) if len(selected) else np.nan
    )
    return selected, {
        **metrics(selected),
        "pool_events": len(pool),
        "pool_runner_arm_rate": arm_rate,
        "runner_arm_auc": _binary_auc(pool["runner_armed"], scores),
        "runner_arm_average_precision": _average_precision(
            pool["runner_armed"], scores
        ),
        "runner_arm_precision": precision,
        "runner_arm_precision_lift_pp": (precision - arm_rate) * 100.0,
        "runner_arm_recall": selected_armed / armed if armed else np.nan,
        "profitable_runner_precision": float(
            (
                selected["runner_armed"].astype(bool)
                & selected["net_return"].gt(0.0)
            ).mean()
        )
        if len(selected)
        else np.nan,
        "positive_net_auc": _binary_auc(pool["net_return"].gt(0.0), scores),
        "selection_rate": len(selected) / len(pool) if len(pool) else np.nan,
    }


def development_oof(
    events: pd.DataFrame,
    tensor: np.ndarray,
    feature_names: Sequence[str],
    config: Mapping[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Run expanding-time OOF screening on 2023 with a 96-bar purge."""

    parent_path = ROOT / str(config["lineage"]["parent_config"]["path"])
    parent = json.loads(parent_path.read_text(encoding="utf-8"))
    variants = list(config["ex_ante_screen"]["registered_variants"])
    quantile = float(config["ex_ante_screen"]["training_quantile"])
    purge = pd.Timedelta(
        minutes=int(config["periods"]["purge_bars"])
        * int(config["periods"]["bar_minutes"])
    )
    scored_parts: list[pd.DataFrame] = []
    fold_rows: list[dict[str, Any]] = []
    for fold in parent["splits"]["development_folds"]:
        fold_id = str(fold["fold"])
        test_start = utc(fold["test_start_inclusive"])
        test_end = utc(fold["test_end_exclusive"])
        train_mask = (
            events["entry_time"].ge(utc(fold["train_start_inclusive"]))
            & events["entry_time"].lt(test_start - purge)
        ).to_numpy()
        test_mask = (
            events["entry_time"].ge(test_start) & events["entry_time"].lt(test_end)
        ).to_numpy()
        train = events.loc[train_mask].reset_index(drop=True)
        test = events.loc[test_mask].reset_index(drop=True)
        for variant in variants:
            contract, train_scores = fit_variant(
                variant, train, tensor[train_mask], feature_names, config
            )
            test_scores = predict_variant(
                contract, train, test, tensor[test_mask]
            )
            threshold = threshold_for(variant, train_scores, quantile)
            selected, row = evaluate_selection(test, test_scores, threshold)
            fold_rows.append(
                {
                    "fold": fold_id,
                    "variant": variant,
                    "train_events": len(train),
                    "test_events": len(test),
                    "threshold": threshold,
                    **row,
                }
            )
            compact = test[
                [
                    "setup_id",
                    "signal_time",
                    "entry_time",
                    "direction",
                    "runner_armed",
                    "gross_return",
                    "net_return",
                    "net_return_r",
                    "hold_bars",
                    "horizon_mfe_atr",
                    "capture_of_horizon_mfe",
                ]
            ].copy()
            compact["fold"] = fold_id
            compact["variant"] = variant
            compact["model_score"] = test_scores
            compact["score_threshold"] = threshold
            compact["selected"] = test_scores >= threshold
            scored_parts.append(compact)
    scored = pd.concat(scored_parts, ignore_index=True)
    folds = pd.DataFrame(fold_rows)
    summaries: list[dict[str, Any]] = []
    for variant in variants:
        part = scored.loc[scored["variant"].eq(variant)].copy()
        selected = part.loc[part["selected"].astype(bool)]
        # evaluate_selection cannot use fold-specific thresholds; replace its
        # selected-trade fields with the actual concatenated OOF mask.
        arm_rate = float(part["runner_armed"].astype(bool).mean())
        precision = float(selected["runner_armed"].astype(bool).mean())
        selected_armed = int(selected["runner_armed"].astype(bool).sum())
        total_armed = int(part["runner_armed"].astype(bool).sum())
        variant_folds = folds.loc[folds["variant"].eq(variant)]
        summaries.append(
            {
                "variant": variant,
                **metrics(selected),
                "pool_events": len(part),
                "runner_arm_auc": _binary_auc(
                    part["runner_armed"], part["model_score"]
                ),
                "runner_arm_average_precision": _average_precision(
                    part["runner_armed"], part["model_score"]
                ),
                "runner_arm_precision": precision,
                "runner_arm_precision_lift_pp": (precision - arm_rate) * 100.0,
                "runner_arm_recall": selected_armed / total_armed,
                "profitable_runner_precision": float(
                    (
                        selected["runner_armed"].astype(bool)
                        & selected["net_return"].gt(0.0)
                    ).mean()
                ),
                "selection_rate": len(selected) / len(part),
                "positive_fold_count": int(
                    variant_folds["mean_net_bp"].gt(0.0).sum()
                ),
                "worst_fold_net_bp": float(variant_folds["mean_net_bp"].min()),
                "worst_fold_arm_precision_lift_pp": float(
                    variant_folds["runner_arm_precision_lift_pp"].min()
                ),
                "minimum_fold_selected_events": int(
                    variant_folds["events"].min()
                ),
            }
        )
    summary = pd.DataFrame(summaries).sort_values(
        [
            "worst_fold_arm_precision_lift_pp",
            "runner_arm_average_precision",
            "runner_arm_auc",
            "variant",
        ],
        ascending=[False, False, False, True],
        kind="mergesort",
    )
    return scored, folds, summary.reset_index(drop=True)


def frozen_evaluations(
    ledgers: Mapping[str, pd.DataFrame],
    tensors: Mapping[str, np.ndarray],
    feature_names: Sequence[str],
    config: Mapping[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, dict[str, pd.DataFrame]], dict[str, Any]]:
    train = ledgers["development"]
    variants = list(config["ex_ante_screen"]["registered_variants"])
    quantile = float(config["ex_ante_screen"]["training_quantile"])
    sensitivity = list(config["ex_ante_screen"]["sensitivity_quantiles"])
    rows: list[dict[str, Any]] = []
    curves: list[dict[str, Any]] = []
    selections: dict[str, dict[str, pd.DataFrame]] = {}
    contracts: dict[str, Any] = {}
    for variant in variants:
        contract, train_scores = fit_variant(
            variant, train, tensors["development"], feature_names, config
        )
        contracts[variant] = contract
        threshold = threshold_for(variant, train_scores, quantile)
        selections[variant] = {}
        for phase in ("development", "confirmation", "audit"):
            pool = ledgers[phase]
            scores = predict_variant(
                contract, train, pool, tensors[phase]
            )
            selected, row = evaluate_selection(pool, scores, threshold)
            selections[variant][phase] = selected
            rows.append(
                {
                    "variant": variant,
                    "phase": phase,
                    "evidence_role": "in_sample_fit"
                    if phase == "development"
                    else ("exact_replay" if phase == "confirmation" else "diagnostic_audit"),
                    "threshold": threshold,
                    **row,
                }
            )
            if variant != "strict_k1k2_rule":
                for q in sensitivity:
                    q_threshold = float(np.nanquantile(train_scores, float(q)))
                    q_selected, q_row = evaluate_selection(pool, scores, q_threshold)
                    curves.append(
                        {
                            "variant": variant,
                            "phase": phase,
                            "training_quantile": float(q),
                            "threshold": q_threshold,
                            **q_row,
                        }
                    )
        model = contract.get("model")
        if model is not None and str(contract["kind"]) == "lgb_classifier":
            importance = pd.DataFrame(
                {
                    "variant": variant,
                    "feature": contract["features"],
                    "gain_importance": model.booster_.feature_importance(
                        importance_type="gain"
                    ),
                    "split_importance": model.booster_.feature_importance(
                        importance_type="split"
                    ),
                }
            ).sort_values(["gain_importance", "feature"], ascending=[False, True])
            write_csv(importance, RESULTS / f"feature_importance_{variant}.csv")
    return pd.DataFrame(rows), pd.DataFrame(curves), selections, contracts


def single_feature_stability(
    ledgers: Mapping[str, pd.DataFrame], feature_names: Sequence[str]
) -> pd.DataFrame:
    train = ledgers["development"]
    labels = train["runner_armed"].astype(int).to_numpy()
    rows: list[dict[str, Any]] = []
    for column in feature_names:
        values = train[column].replace([np.inf, -np.inf], np.nan).astype(float)
        median = float(values.median())
        median = median if np.isfinite(median) else 0.0
        base = values.fillna(median).to_numpy(dtype=float)
        raw_auc = _binary_auc(labels, base)
        direction = 1 if raw_auc >= 0.5 else -1
        threshold = float(np.quantile(direction * base, 0.9))
        row: dict[str, Any] = {"feature": column, "direction": direction}
        for phase in ("development", "confirmation", "audit"):
            frame = ledgers[phase]
            score = direction * (
                frame[column]
                .replace([np.inf, -np.inf], np.nan)
                .fillna(median)
                .to_numpy(dtype=float)
            )
            selected = frame.loc[score >= threshold]
            row[f"{phase}_auc"] = _binary_auc(frame["runner_armed"], score)
            row[f"{phase}_events"] = len(selected)
            row[f"{phase}_arm_precision"] = float(
                selected["runner_armed"].astype(bool).mean()
            )
            row[f"{phase}_net_bp"] = float(selected["net_return"].mean() * 1e4)
        row["minimum_future_auc"] = min(
            float(row["confirmation_auc"]), float(row["audit_auc"])
        )
        rows.append(row)
    return pd.DataFrame(rows).sort_values(
        ["minimum_future_auc", "development_auc", "feature"],
        ascending=[False, False, True],
        kind="mergesort",
    )


def _simple_trade_metrics(events: pd.DataFrame, net_column: str = "net_return") -> dict[str, Any]:
    values = events[net_column].astype(float).to_numpy()
    positive = float(values[values > 0.0].sum())
    negative = float(-values[values < 0.0].sum())
    return {
        "events": len(events),
        "mean_net_bp": float(np.mean(values) * 1e4) if len(values) else np.nan,
        "median_net_bp": float(np.median(values) * 1e4) if len(values) else np.nan,
        "win_rate": float(np.mean(values > 0.0)) if len(values) else np.nan,
        "profit_factor": positive / negative if negative > 0.0 else np.nan,
        "max_net_bp": float(np.max(values) * 1e4) if len(values) else np.nan,
    }


def _stop_fill(open_price: float, stop: float, direction: int) -> float:
    return min(open_price, stop) if direction > 0 else max(open_price, stop)


def delayed_confirmation_entries(
    market: pd.DataFrame,
    events: pd.DataFrame,
    *,
    cost: float,
) -> pd.DataFrame:
    """Enter only after the original position has close-confirmed +2ATR.

    The arm close at ``i`` is known only after that bar completes, so the new
    entry fills at ``open[i+1]``.  The original active stop after the arm close
    becomes effective on that next bar.  This directly tests the tempting but
    invalid shortcut of treating outcome-conditioned runner trades as entries.
    """

    open_ = market["open"].to_numpy(dtype=float)
    high = market["high"].to_numpy(dtype=float)
    low = market["low"].to_numpy(dtype=float)
    close = market["close"].to_numpy(dtype=float)
    atr = market["atr"].to_numpy(dtype=float)
    trend = market["trend_ma"].to_numpy(dtype=float)
    segments = market["segment_id"].to_numpy(dtype=int)
    rows: list[dict[str, Any]] = []
    for event in events.itertuples(index=False):
        if not bool(event.runner_armed) or pd.isna(event.runner_arm_i):
            continue
        original_entry_i = int(event.entry_i)
        arm_i = int(event.runner_arm_i)
        entry_i = arm_i + 1
        end_i = min(original_entry_i + 95, len(market) - 1)
        if entry_i > end_i or segments[entry_i] != segments[original_entry_i]:
            continue
        direction = int(event.direction)
        entry = float(open_[entry_i])
        signal_atr = float(event.signal_atr)
        hard_stop = float(event.entry_price) - direction * 2.0 * signal_atr
        candidate = trend[arm_i] - direction * atr[arm_i]
        active_stop = (
            max(hard_stop, candidate) if direction > 0 else min(hard_stop, candidate)
        )
        exit_i: int | None = None
        exit_price: float | None = None
        outcome = ""
        for i in range(entry_i, end_i + 1):
            hit = low[i] <= active_stop if direction > 0 else high[i] >= active_stop
            if hit:
                exit_i = i
                exit_price = _stop_fill(open_[i], active_stop, direction)
                outcome = "post_arm_trail_stop"
                break
            candidate = trend[i] - direction * atr[i]
            active_stop = (
                max(active_stop, candidate)
                if direction > 0
                else min(active_stop, candidate)
            )
        if exit_i is None:
            exit_i = end_i
            exit_price = close[end_i]
            outcome = "post_arm_timeout"
        gross = direction * (float(exit_price) / entry - 1.0)
        rows.append(
            {
                "setup_id": event.setup_id,
                "direction": direction,
                "signal_time": event.signal_time,
                "entry_i": entry_i,
                "entry_price": entry,
                "arm_delay_bars": entry_i - original_entry_i,
                "exit_i": exit_i,
                "exit_price": exit_price,
                "outcome": outcome,
                "gross_return": gross,
                "net_return": gross - cost,
                "hold_bars": exit_i - entry_i + 1,
            }
        )
    return pd.DataFrame(rows)


def simulate_progress_stop(
    market: pd.DataFrame,
    events: pd.DataFrame,
    *,
    deadline_bars: int,
    required_close_atr: float,
    cost: float,
) -> pd.DataFrame:
    """Exit next open when launch progress is insufficient before runner arm."""

    open_ = market["open"].to_numpy(dtype=float)
    high = market["high"].to_numpy(dtype=float)
    low = market["low"].to_numpy(dtype=float)
    close = market["close"].to_numpy(dtype=float)
    atr = market["atr"].to_numpy(dtype=float)
    trend = market["trend_ma"].to_numpy(dtype=float)
    segments = market["segment_id"].to_numpy(dtype=int)
    rows: list[dict[str, Any]] = []
    for event in events.itertuples(index=False):
        entry_i = int(event.entry_i)
        direction = int(event.direction)
        entry = float(event.entry_price)
        signal_atr = float(event.signal_atr)
        end_i = min(entry_i + 95, len(market) - 1)
        if segments[end_i] != segments[entry_i]:
            continue
        active_stop = entry - direction * 2.0 * signal_atr
        armed = False
        max_close_profit = -np.inf
        exit_i: int | None = None
        exit_price: float | None = None
        outcome = ""
        for i in range(entry_i, end_i + 1):
            hit = low[i] <= active_stop if direction > 0 else high[i] >= active_stop
            if hit:
                exit_i = i
                exit_price = _stop_fill(open_[i], active_stop, direction)
                outcome = "active_stop"
                break
            close_profit = direction * (close[i] - entry) / signal_atr
            max_close_profit = max(max_close_profit, close_profit)
            if not armed and close_profit >= 2.0:
                armed = True
            if armed:
                candidate = trend[i] - direction * atr[i]
                active_stop = (
                    max(active_stop, candidate)
                    if direction > 0
                    else min(active_stop, candidate)
                )
            elif (
                i - entry_i + 1 >= deadline_bars
                and max_close_profit < required_close_atr
                and i + 1 <= end_i
            ):
                exit_i = i + 1
                exit_price = open_[i + 1]
                outcome = "progress_exit_next_open"
                break
        if exit_i is None:
            exit_i = end_i
            exit_price = close[end_i]
            outcome = "timeout"
        gross = direction * (float(exit_price) / entry - 1.0)
        rows.append(
            {
                "setup_id": event.setup_id,
                "direction": direction,
                "signal_time": event.signal_time,
                "entry_i": entry_i,
                "entry_price": entry,
                "exit_i": exit_i,
                "exit_price": exit_price,
                "outcome": outcome,
                "gross_return": gross,
                "net_return": gross - cost,
                "hold_bars": exit_i - entry_i + 1,
                "runner_armed": armed,
            }
        )
    return pd.DataFrame(rows)


def progress_stop_grid(
    market: pd.DataFrame,
    ledgers: Mapping[str, pd.DataFrame],
    config: Mapping[str, Any],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    cost = float(config["execution_frozen"]["round_trip_cost_fraction"])
    spec = config["causal_management_probes"]
    for deadline in spec["progress_stop_deadline_bars"]:
        for progress in spec["progress_stop_required_close_atr"]:
            for phase in ("development", "confirmation", "audit"):
                result = simulate_progress_stop(
                    market,
                    ledgers[phase],
                    deadline_bars=int(deadline),
                    required_close_atr=float(progress),
                    cost=cost,
                )
                rows.append(
                    {
                        "deadline_bars": int(deadline),
                        "required_close_atr": float(progress),
                        "phase": phase,
                        **_simple_trade_metrics(result),
                        "runner_arm_rate": float(result["runner_armed"].mean()),
                        "progress_exit_rate": float(
                            result["outcome"].eq("progress_exit_next_open").mean()
                        ),
                    }
                )
    return pd.DataFrame(rows)


def attach_early_features(
    market: pd.DataFrame, events: pd.DataFrame, *, checkpoint_bars: int
) -> pd.DataFrame:
    """Attach path features ending at the registered checkpoint close."""

    open_ = market["open"].to_numpy(dtype=float)
    high = market["high"].to_numpy(dtype=float)
    low = market["low"].to_numpy(dtype=float)
    close = market["close"].to_numpy(dtype=float)
    volume = market["volume"].to_numpy(dtype=float)
    atr = market["atr"].to_numpy(dtype=float)
    ema30 = market["ema30"].to_numpy(dtype=float)
    sma60 = market["sma60"].to_numpy(dtype=float)
    volume_median = market["volume_median20_prior"].to_numpy(dtype=float)
    rows: list[list[float]] = []
    for event in events.itertuples(index=False):
        entry_i = int(event.entry_i)
        indices = np.arange(entry_i, entry_i + checkpoint_bars)
        direction = int(event.direction)
        entry = float(event.entry_price)
        signal_atr = float(event.signal_atr)
        close_profit = direction * (close[indices] - entry) / signal_atr
        favourable = (
            (high[indices] - entry) / signal_atr
            if direction > 0
            else (entry - low[indices]) / signal_atr
        )
        adverse = (
            (entry - low[indices]) / signal_atr
            if direction > 0
            else (high[indices] - entry) / signal_atr
        )
        ranges = (high[indices] - low[indices]) / atr[indices]
        volume_ratio = volume[indices] / np.maximum(volume_median[indices], 1e-12)
        ema_distance = direction * (close[indices] - ema30[indices]) / atr[indices]
        sma_distance = direction * (close[indices] - sma60[indices]) / atr[indices]
        slope = (
            float(np.polyfit(np.arange(checkpoint_bars), close_profit, 1)[0])
            if checkpoint_bars > 1
            else float(close_profit[-1])
        )
        rows.append(
            [
                close_profit[-1],
                np.max(close_profit),
                np.min(close_profit),
                np.max(favourable),
                np.max(adverse),
                np.mean(close_profit),
                slope,
                np.mean(close_profit > 0.0),
                np.mean(np.diff(close_profit) > 0.0) if checkpoint_bars > 1 else 0.0,
                np.nanmean(ranges),
                np.nanmax(ranges),
                np.nanmean(volume_ratio),
                np.nanmax(volume_ratio),
                ema_distance[-1],
                sma_distance[-1],
                np.mean(ema_distance > 0.0),
                np.mean(sma_distance > 0.0),
            ]
        )
    early = pd.DataFrame(rows, columns=EARLY_COLUMNS, index=events.index)
    return pd.concat([events, early], axis=1)


def early_classifier_grid(
    market: pd.DataFrame,
    ledgers: Mapping[str, pd.DataFrame],
    feature_names: Sequence[str],
    config: Mapping[str, Any],
) -> pd.DataFrame:
    """Fit at each checkpoint, then replace rejected trades by next-open exits."""

    rows: list[dict[str, Any]] = []
    open_ = market["open"].to_numpy(dtype=float)
    cost = float(config["execution_frozen"]["round_trip_cost_fraction"])
    for checkpoint in config["causal_management_probes"][
        "early_classifier_checkpoints_bars"
    ]:
        featured = {
            phase: attach_early_features(
                market, ledgers[phase], checkpoint_bars=int(checkpoint)
            )
            for phase in ("development", "confirmation", "audit")
        }
        train_all = featured["development"]
        train_decision_i = train_all["entry_i"].astype(int) + int(checkpoint) - 1
        train_already = train_all["runner_armed"].astype(bool) & train_all[
            "runner_arm_i"
        ].fillna(10**9).astype(int).le(train_decision_i)
        train_unavoidable = train_all["exit_i"].astype(int).lt(train_decision_i + 1)
        train_mask = ~(train_already | train_unavoidable)
        train = train_all.loc[train_mask].reset_index(drop=True)
        columns = list(feature_names) + EARLY_COLUMNS
        contract, train_scores = _fit_lgb_classifier(
            train, columns, train["runner_armed"], config
        )
        for phase in ("development", "confirmation", "audit"):
            frame = featured[phase]
            decision_i = frame["entry_i"].astype(int) + int(checkpoint) - 1
            already = frame["runner_armed"].astype(bool) & frame[
                "runner_arm_i"
            ].fillna(10**9).astype(int).le(decision_i)
            unavoidable = frame["exit_i"].astype(int).lt(decision_i + 1)
            eligible = ~(already | unavoidable)
            scores = np.full(len(frame), np.nan)
            test = frame.loc[eligible].reset_index(drop=True)
            scores[eligible.to_numpy()] = predict_variant(
                contract, train, test, np.empty((len(test), 0, 0))
            )
            eligible_labels = frame.loc[eligible, "runner_armed"].astype(bool)
            eligible_auc = _binary_auc(eligible_labels, scores[eligible.to_numpy()])
            eligible_ap = _average_precision(
                eligible_labels, scores[eligible.to_numpy()]
            )
            for quantile in config["causal_management_probes"][
                "early_classifier_keep_training_quantiles"
            ]:
                threshold = float(np.quantile(train_scores, float(quantile)))
                keep = already.to_numpy() | (
                    eligible.to_numpy() & (scores >= threshold)
                )
                reject = eligible.to_numpy() & ~keep
                net = frame["net_return"].to_numpy(dtype=float).copy()
                next_indices = (decision_i + 1).to_numpy(dtype=int)
                net[reject] = (
                    frame["direction"].to_numpy(dtype=int)[reject]
                    * (
                        open_[next_indices[reject]]
                        / frame["entry_price"].to_numpy(dtype=float)[reject]
                        - 1.0
                    )
                    - cost
                )
                armed = frame["runner_armed"].astype(bool).to_numpy()
                kept_armed = int(np.count_nonzero(keep & armed))
                total_armed = int(np.count_nonzero(armed))
                result = pd.DataFrame({"net_return": net})
                rows.append(
                    {
                        "checkpoint_bars": int(checkpoint),
                        "keep_training_quantile": float(quantile),
                        "phase": phase,
                        "eligible_events": int(eligible.sum()),
                        "eligible_runner_arm_rate": float(eligible_labels.mean()),
                        "eligible_runner_arm_auc": eligible_auc,
                        "eligible_runner_arm_average_precision": eligible_ap,
                        "keep_rate": float(np.mean(keep)),
                        "reject_rate": float(np.mean(reject)),
                        "runner_arm_recall": kept_armed / total_armed,
                        **_simple_trade_metrics(result),
                    }
                )
    return pd.DataFrame(rows)


def _half_year_positive(events: pd.DataFrame) -> bool:
    labels = pd.to_datetime(events["entry_time"], utc=True).map(
        lambda stamp: f"{stamp.year}H{1 if stamp.month <= 6 else 2}"
    )
    means = events.groupby(labels)["net_return"].mean()
    return bool(len(means) and means.gt(0.0).all())


def main() -> None:
    config = load_config()
    verify_sources(config)
    RESULTS.mkdir(parents=True, exist_ok=True)
    ledgers = load_ledgers(config)
    market, market_receipt = load_market(config)
    feature_names = parent_feature_names(config)
    missing = sorted(set(feature_names) - set(ledgers["development"].columns))
    if missing:
        raise RuntimeError(f"parent causal features missing: {missing}")

    window = int(config["ex_ante_screen"]["aeon"]["window_bars"])
    tensors = {
        phase: sequence_tensor(market, ledgers[phase], window=window)
        for phase in ("development", "confirmation", "audit")
    }
    oof_scores, oof_folds, oof_summary = development_oof(
        ledgers["development"], tensors["development"], feature_names, config
    )
    frozen, density, selections, _ = frozen_evaluations(
        ledgers, tensors, feature_names, config
    )
    stability = single_feature_stability(ledgers, feature_names)

    eligible = oof_summary.loc[
        oof_summary["events"].ge(120)
        & oof_summary["minimum_fold_selected_events"].ge(30)
    ]
    nominated = str((eligible if len(eligible) else oof_summary).iloc[0]["variant"])
    inference = config["inference"]
    control_rows: list[dict[str, Any]] = []
    control_artifacts: dict[str, dict[str, pd.DataFrame]] = {}
    for phase, period_key in (
        ("confirmation", "confirmation"),
        ("audit", "diagnostic_audit"),
    ):
        pool = ledgers[phase]
        selected = selections[nominated][phase]
        start, end = map(utc, config["periods"][period_key])
        controls, pairs, control_metrics = _matched_control_metrics(
            selected, market, config, start, end
        )
        permutation = score_permutation_p(
            pool,
            selected,
            resamples=int(inference["score_permutation_resamples"]),
            seed=int(inference["seed"]),
        )
        weekly = (
            selected.assign(
                week=pd.to_datetime(selected["entry_time"], utc=True).dt.strftime(
                    "%G-%V"
                )
            )
            .groupby("week", sort=True)["net_return"]
            .mean()
            .to_numpy(dtype=float)
        )
        weekly_p = signflip_p(
            weekly,
            resamples=int(inference["score_permutation_resamples"]),
            seed=int(inference["seed"]),
        )
        control_rows.append(
            {
                "phase": phase,
                "variant": nominated,
                "score_permutation_p_one_sided": permutation,
                "weekly_block_signflip_p_one_sided": weekly_p,
                **control_metrics,
            }
        )
        control_artifacts[phase] = {"controls": controls, "pairs": pairs}

    cost = float(config["execution_frozen"]["round_trip_cost_fraction"])
    delayed_rows: list[dict[str, Any]] = []
    delayed_artifacts: dict[str, pd.DataFrame] = {}
    for phase in ("development", "confirmation", "audit", "audit_selected"):
        result = delayed_confirmation_entries(
            market, ledgers[phase], cost=cost
        )
        delayed_artifacts[phase] = result
        delayed_rows.append(
            {
                "phase": phase,
                "source_events": len(ledgers[phase]),
                "source_runner_armed": int(
                    ledgers[phase]["runner_armed"].astype(bool).sum()
                ),
                "median_arm_delay_bars": float(result["arm_delay_bars"].median()),
                **_simple_trade_metrics(result),
            }
        )
    delayed = pd.DataFrame(delayed_rows)
    progress = progress_stop_grid(market, ledgers, config)
    early = early_classifier_grid(market, ledgers, feature_names, config)

    write_csv(oof_scores, RESULTS / "development_oof_scores.csv.gz")
    write_csv(oof_folds, RESULTS / "development_oof_fold_metrics.csv")
    write_csv(oof_summary, RESULTS / "development_oof_variant_summary.csv")
    write_csv(frozen, RESULTS / "frozen_exante_metrics.csv")
    write_csv(density, RESULTS / "frozen_density_sensitivity.csv")
    write_csv(stability, RESULTS / "single_feature_stability.csv")
    write_csv(pd.DataFrame(control_rows), RESULTS / "matched_control_metrics.csv")
    for phase, artifacts in control_artifacts.items():
        write_csv(artifacts["controls"], RESULTS / f"{phase}_controls.csv.gz")
        write_csv(artifacts["pairs"], RESULTS / f"{phase}_control_pairs.csv")
    write_csv(delayed, RESULTS / "delayed_confirmation_entry_metrics.csv")
    for phase, frame in delayed_artifacts.items():
        write_csv(frame, RESULTS / f"{phase}_delayed_confirmation_trades.csv.gz")
    write_csv(progress, RESULTS / "progress_stop_grid.csv")
    write_csv(early, RESULTS / "early_classifier_grid.csv")

    confirm_row = frozen.loc[
        frozen["variant"].eq(nominated) & frozen["phase"].eq("confirmation")
    ].iloc[0]
    confirm_control = next(row for row in control_rows if row["phase"] == "confirmation")
    gates = {
        "runner_arm_auc": bool(
            float(confirm_row["runner_arm_auc"])
            >= float(config["success_gates"]["runner_arm_auc_min"])
        ),
        "q90_arm_precision_lift": bool(
            float(confirm_row["runner_arm_precision_lift_pp"])
            >= 100.0
            * float(config["success_gates"]["q90_arm_precision_lift_min"])
        ),
        "q90_net_mean": bool(float(confirm_row["mean_net_bp"]) > 0.0),
        "q90_profit_factor": bool(float(confirm_row["profit_factor"]) > 1.0),
        "q90_minimum_events": bool(
            int(confirm_row["events"])
            >= int(config["success_gates"]["q90_minimum_events"])
        ),
        "score_permutation_p": bool(
            float(confirm_control["score_permutation_p_one_sided"])
            < float(config["success_gates"]["score_permutation_p_max"])
        ),
        "matched_control_p": bool(
            float(confirm_control["paired_control_signflip_p_one_sided"])
            < float(config["success_gates"]["matched_control_p_max"])
        ),
        "all_complete_half_year_slices_positive": _half_year_positive(
            selections[nominated]["confirmation"]
        ),
    }
    gates["all_pass"] = all(gates.values())

    audit_selected = ledgers["audit_selected"]
    armed = audit_selected.loc[audit_selected["runner_armed"].astype(bool)]
    unarmed = audit_selected.loc[~audit_selected["runner_armed"].astype(bool)]
    summary = {
        "experiment_id": EXPERIMENT_ID,
        "phase": "retrospective_preholdout_diagnostic_complete",
        "config_sha256": sha256_file(CONFIG_PATH),
        "script_sha256": sha256_file(SCRIPT_PATH),
        "source": market_receipt,
        "feature_count": len(feature_names),
        "sequence_shape": {
            phase: list(tensor.shape) for phase, tensor in tensors.items()
        },
        "parent_outcome_conditioned_decomposition": {
            "cohort_events": len(audit_selected),
            "runner_armed_events": len(armed),
            "runner_armed_mean_net_bp": float(armed["net_return"].mean() * 1e4),
            "never_armed_events": len(unarmed),
            "never_armed_mean_net_bp": float(unarmed["net_return"].mean() * 1e4),
        },
        "nominated_diagnostic_variant": nominated,
        "development_oof": oof_summary.iloc[0].to_dict(),
        "confirmation": confirm_row.to_dict(),
        "confirmation_control": confirm_control,
        "gates": gates,
        "holdout_rows_read": 0,
        "production_eligible": False,
        "interpretation": (
            "Outcome-conditioned runner value is real, but no registered causal "
            "entry-time selector or management probe passed economic and transport gates."
        ),
    }
    write_json(RESULTS / "summary.json", summary)
    print(json.dumps(json_value(summary), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
