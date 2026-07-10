"""Fixed-cost economics and numeric baseline for causal direction predictions.

The module evaluates long/short/no-trade decisions on one immutable val
manifest. It never searches a confidence threshold and has no holdout or
runtime-promotion entrypoint.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import lightgbm as lgb
import numpy as np
import pandas as pd

from src.judgment.features import FEATURE_COLUMNS

DIRECTION_CLASSES: Final = ("long", "short", "no_trade")
DEFAULT_COSTS: Final = (0.0006, 0.002, 0.003)
CLASS_TO_INDEX: Final = {name: index for index, name in enumerate(DIRECTION_CLASSES)}


class DirectionPredictionError(RuntimeError):
    """Raised when predictions cannot be reconciled to the fixed manifest."""


@dataclass(frozen=True)
class CostMetrics:
    round_trip_cost: float
    net_mean_per_trade: float | None
    profit_factor: float | None
    max_drawdown_pct: float | None


@dataclass(frozen=True)
class DirectionEconomicsResult:
    n_candidates: int
    n_trades: int
    trade_coverage: float
    gross_mean_per_trade: float | None
    gross_win_rate: float | None
    cost_metrics: tuple[CostMetrics, ...]


@dataclass(frozen=True)
class NumericBaselineResult:
    class_names: tuple[str, str, str]
    predictions: list[str]
    probabilities: np.ndarray


def _profit_factor(returns: np.ndarray) -> float | None:
    gains = float(returns[returns > 0].sum())
    losses = float(-returns[returns < 0].sum())
    if losses <= 0:
        return None
    return gains / losses


def _max_drawdown(returns: np.ndarray) -> float | None:
    if len(returns) == 0:
        return None
    if np.any(returns <= -1):
        raise DirectionPredictionError("net return must remain greater than -100%")
    equity = np.cumprod(1.0 + returns)
    peaks = np.maximum.accumulate(np.concatenate(([1.0], equity)))
    drawdowns = (peaks[1:] - equity) / peaks[1:]
    return float(drawdowns.max(initial=0.0))


def evaluate_direction_predictions(
    manifest: pd.DataFrame,
    predictions: list[str],
    *,
    costs: tuple[float, ...] = DEFAULT_COSTS,
) -> DirectionEconomicsResult:
    """Map fixed classes to side returns and evaluate only predicted trades."""
    if len(predictions) != len(manifest):
        raise DirectionPredictionError(
            f"prediction length={len(predictions)} differs from manifest length={len(manifest)}"
        )
    unsupported = sorted(set(predictions) - set(DIRECTION_CLASSES))
    if unsupported:
        raise DirectionPredictionError(f"unsupported direction classes: {unsupported}")

    selected: list[float] = []
    for row, prediction in zip(manifest.itertuples(index=False), predictions):
        if prediction == "no_trade":
            continue
        column = "long_realized_ret" if prediction == "long" else "short_realized_ret"
        value = float(getattr(row, column))
        if not np.isfinite(value):
            raise DirectionPredictionError(f"selected {column} must be finite")
        selected.append(value)

    gross = np.asarray(selected, dtype=float)
    n_candidates = len(manifest)
    n_trades = len(gross)
    cost_metrics = tuple(
        CostMetrics(
            round_trip_cost=cost,
            net_mean_per_trade=float((gross - cost).mean()) if n_trades else None,
            profit_factor=_profit_factor(gross - cost) if n_trades else None,
            max_drawdown_pct=_max_drawdown(gross - cost),
        )
        for cost in costs
    )
    return DirectionEconomicsResult(
        n_candidates=n_candidates,
        n_trades=n_trades,
        trade_coverage=n_trades / n_candidates if n_candidates else 0.0,
        gross_mean_per_trade=float(gross.mean()) if n_trades else None,
        gross_win_rate=float((gross > 0).mean()) if n_trades else None,
        cost_metrics=cost_metrics,
    )


def train_numeric_direction_baseline(
    train: pd.DataFrame,
    val: pd.DataFrame,
) -> NumericBaselineResult:
    """Fit one fixed multiclass LightGBM baseline on existing causal features."""
    labels = set(train["direction_class"].astype(str))
    unsupported = sorted(labels - set(DIRECTION_CLASSES))
    if unsupported:
        raise DirectionPredictionError(f"unsupported training direction classes: {unsupported}")
    missing = sorted(set(DIRECTION_CLASSES) - labels)
    if missing:
        raise DirectionPredictionError(f"missing training direction classes: {missing}")

    train_y = train["direction_class"].map(CLASS_TO_INDEX).to_numpy(dtype=int)
    val_y = val["direction_class"].map(CLASS_TO_INDEX).to_numpy(dtype=int)
    dtrain = lgb.Dataset(train[FEATURE_COLUMNS].fillna(0.0), label=train_y)
    dval = lgb.Dataset(val[FEATURE_COLUMNS].fillna(0.0), label=val_y, reference=dtrain)
    model = lgb.train(
        {
            "objective": "multiclass",
            "num_class": len(DIRECTION_CLASSES),
            "learning_rate": 0.05,
            "num_leaves": 15,
            "min_child_samples": 10,
            "lambda_l2": 1.0,
            "seed": 42,
            "verbosity": -1,
            "num_threads": 1,
        },
        dtrain,
        num_boost_round=200,
        valid_sets=[dval],
        callbacks=[lgb.early_stopping(20, verbose=False)],
    )
    probabilities = np.asarray(
        model.predict(val[FEATURE_COLUMNS].fillna(0.0), num_iteration=model.best_iteration)
    )
    predictions = [DIRECTION_CLASSES[index] for index in probabilities.argmax(axis=1)]
    return NumericBaselineResult(DIRECTION_CLASSES, predictions, probabilities)
