"""Isolated booster challengers for pre-holdout judgment diagnostics.

All models consume the same frozen feature order and chronological split. This
module never saves a model, selects an ACTIVE artifact, or reads judgment rows
at or beyond the sealed holdout boundary.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final

import numpy as np
import pandas as pd

from src.judgment.features import FEATURE_COLUMNS
from src.judgment.frozen import read_dataset_before
from src.judgment.shadow_booster_types import (
    EvaluationMetrics,
    ModelBenchmark,
    ShadowBenchmark,
    ShadowDataError,
    ShadowDependencyError,
    ThresholdMetrics,
    TrainedShadowModel,
)
from src.judgment.train import (
    HOLDOUT_START,
    PURGE_WINDOW,
    SEED,
    TRAIN_FRACTION,
    evaluate,
    permutation_pvalue,
    train_model,
)

BASE_MODELS: Final = ("lightgbm", "catboost", "xgboost")
ENSEMBLE_NAME: Final = "ensemble"


def load_shadow_splits(dataset_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load only pre-holdout rows, then apply the incumbent time split and purge."""
    dev_cutoff = pd.Timestamp(str(HOLDOUT_START - PURGE_WINDOW))
    assert isinstance(dev_cutoff, pd.Timestamp)
    dev = read_dataset_before(dataset_path, end_before=dev_cutoff)
    required = {"signal_time", "label", "realized_ret", *FEATURE_COLUMNS}
    missing = sorted(required.difference(dev.columns))
    if missing:
        raise ShadowDataError(f"dataset missing columns: {', '.join(missing)}")
    dev = dev.sort_values("signal_time").reset_index(drop=True)
    split_i = int(len(dev) * TRAIN_FRACTION)
    val = pd.DataFrame(dev.iloc[split_i:]).reset_index(drop=True)
    if val.empty:
        raise ShadowDataError("validation split is empty")
    val_start = pd.Timestamp(str(val["signal_time"].min()))
    train = pd.DataFrame(dev.loc[dev["signal_time"] < val_start - PURGE_WINDOW]).reset_index(drop=True)
    if train.empty:
        raise ShadowDataError("training split is empty after purge")
    return train, val


def equal_weight_probabilities(predictions: dict[str, np.ndarray]) -> np.ndarray:
    if len(predictions) < 2:
        raise ShadowDataError("ensemble requires at least two models")
    lengths = {len(np.asarray(values).reshape(-1)) for values in predictions.values()}
    if len(lengths) != 1:
        raise ShadowDataError("prediction lengths differ")
    matrix = np.vstack([np.asarray(values, dtype=float).reshape(-1) for values in predictions.values()])
    return matrix.mean(axis=0)


def run_shadow_benchmark(
    train: pd.DataFrame,
    val: pd.DataFrame,
    model_names: Sequence[str],
) -> ShadowBenchmark:
    requested = tuple(dict.fromkeys(model_names))
    unknown = sorted(set(requested).difference((*BASE_MODELS, ENSEMBLE_NAME)))
    if unknown:
        raise ShadowDataError(f"unknown models: {', '.join(unknown)}")
    trainers: dict[str, Callable[[pd.DataFrame, pd.DataFrame], TrainedShadowModel]] = {
        "lightgbm": _train_lightgbm,
        "catboost": _train_catboost,
        "xgboost": _train_xgboost,
    }
    runs: dict[str, ModelBenchmark] = {}
    predictions: dict[str, np.ndarray] = {}
    trained: list[TrainedShadowModel] = []
    for name in requested:
        trainer = trainers.get(name)
        if trainer is None:
            continue
        model = trainer(train, val)
        trained.append(model)
        probabilities = model.predict(pd.DataFrame(val.loc[:, FEATURE_COLUMNS]))
        predictions[name] = probabilities
        runs[name] = _model_metrics(model, val, probabilities)

    if ENSEMBLE_NAME in requested:
        ensemble_probabilities = equal_weight_probabilities(predictions)
        ensemble_model = TrainedShadowModel(
            name=ENSEMBLE_NAME,
            predict=lambda frame: equal_weight_probabilities(
                {model.name: model.predict(frame) for model in trained}
            ),
            best_iteration=0,
            fit_seconds=sum(model.fit_seconds for model in trained),
        )
        runs[ENSEMBLE_NAME] = _model_metrics(ensemble_model, val, ensemble_probabilities)

    score_frame = pd.DataFrame(predictions)
    correlation = score_frame.corr(method="spearman")
    correlation_values = {
        column: {row: float(correlation.loc[row, column]) for row in correlation.index}
        for column in correlation.columns
    }
    return {
        "models": runs,
        "base_score_spearman": correlation_values,
        "warning": "Diagnostic on reused pre-holdout val; not model-selection or profitability evidence.",
    }


def _train_lightgbm(train: pd.DataFrame, val: pd.DataFrame) -> TrainedShadowModel:
    started = time.perf_counter()
    model = train_model(train, val)
    elapsed = time.perf_counter() - started
    best_iteration = int(model.best_iteration or model.current_iteration())
    return TrainedShadowModel(
        name="lightgbm",
        predict=lambda frame: np.asarray(
            model.predict(frame[FEATURE_COLUMNS], num_iteration=best_iteration), dtype=float
        ),
        best_iteration=best_iteration,
        fit_seconds=elapsed,
    )


def _train_catboost(train: pd.DataFrame, val: pd.DataFrame) -> TrainedShadowModel:
    try:
        from catboost import CatBoostClassifier
    except ImportError as exc:
        raise ShadowDependencyError("catboost") from exc
    model = CatBoostClassifier(
        iterations=600,
        depth=6,
        learning_rate=0.05,
        loss_function="Logloss",
        eval_metric="AUC",
        l2_leaf_reg=3.0,
        random_seed=SEED,
        has_time=True,
        allow_writing_files=False,
        verbose=False,
        thread_count=4,
    )
    started = time.perf_counter()
    model.fit(
        train[FEATURE_COLUMNS],
        train["label"],
        eval_set=(val[FEATURE_COLUMNS], val["label"]),
        early_stopping_rounds=50,
        use_best_model=True,
        verbose=False,
    )
    elapsed = time.perf_counter() - started
    return TrainedShadowModel(
        name="catboost",
        predict=lambda frame: np.asarray(model.predict_proba(frame[FEATURE_COLUMNS])[:, 1], dtype=float),
        best_iteration=int(model.get_best_iteration()) + 1,
        fit_seconds=elapsed,
    )


def _train_xgboost(train: pd.DataFrame, val: pd.DataFrame) -> TrainedShadowModel:
    try:
        from xgboost import XGBClassifier
    except ImportError as exc:
        raise ShadowDependencyError("xgboost") from exc
    model = XGBClassifier(
        n_estimators=600,
        learning_rate=0.05,
        max_depth=4,
        min_child_weight=30,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_lambda=1.0,
        objective="binary:logistic",
        eval_metric="auc",
        tree_method="hist",
        early_stopping_rounds=50,
        random_state=SEED,
        n_jobs=4,
    )
    started = time.perf_counter()
    model.fit(
        train[FEATURE_COLUMNS],
        train["label"],
        eval_set=[(val[FEATURE_COLUMNS], val["label"])],
        verbose=False,
    )
    elapsed = time.perf_counter() - started
    return TrainedShadowModel(
        name="xgboost",
        predict=lambda frame: np.asarray(model.predict_proba(frame[FEATURE_COLUMNS])[:, 1], dtype=float),
        best_iteration=int(model.best_iteration) + 1,
        fit_seconds=elapsed,
    )


def _model_metrics(
    model: TrainedShadowModel,
    val: pd.DataFrame,
    probabilities: np.ndarray,
) -> ModelBenchmark:
    feature_frame = pd.DataFrame(val.loc[:, FEATURE_COLUMNS])
    single_row = pd.DataFrame(feature_frame.iloc[:1])
    model.predict(single_row)
    single_started = time.perf_counter()
    for _ in range(50):
        model.predict(single_row)
    single_ms = (time.perf_counter() - single_started) * 1000 / 50
    batch_started = time.perf_counter()
    for _ in range(10):
        model.predict(feature_frame)
    batch_us_per_row = (time.perf_counter() - batch_started) * 1_000_000 / (10 * len(feature_frame))
    raw_evaluation = evaluate(
        val["label"].to_numpy(),
        probabilities,
        val["realized_ret"].to_numpy(),
    )
    thresholds: dict[str, ThresholdMetrics] = {
        str(threshold): {
            "n_signals": int(values["n_signals"]),
            "precision": float(values["precision"]),
            "recall": float(values["recall"]),
        }
        for threshold, values in raw_evaluation["thresholds"].items()
    }
    evaluation: EvaluationMetrics = {
        "n": int(raw_evaluation["n"]),
        "positive_rate": float(raw_evaluation["positive_rate"]),
        "roc_auc": float(raw_evaluation["roc_auc"]),
        "pr_auc": float(raw_evaluation["pr_auc"]),
        "thresholds": thresholds,
        "top_decile": {
            "n": int(raw_evaluation["top_decile"]["n"]),
            "mean_realized_ret": float(raw_evaluation["top_decile"]["mean_realized_ret"]),
            "mean_net_ret": float(raw_evaluation["top_decile"]["mean_net_ret"]),
            "win_rate": float(raw_evaluation["top_decile"]["win_rate"]),
        },
        "all_mean_net_ret": float(raw_evaluation["all_mean_net_ret"]),
    }
    return {
        "best_iteration": model.best_iteration,
        "fit_seconds": model.fit_seconds,
        "single_row_ms": single_ms,
        "batch_us_per_row": batch_us_per_row,
        "val": evaluation,
        "val_permutation_p": permutation_pvalue(val["label"].to_numpy(), probabilities),
    }
