"""Typed contracts for isolated judgment booster benchmarks."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypedDict

import numpy as np
import pandas as pd


class ShadowDataError(ValueError):
    pass


class ShadowDependencyError(RuntimeError):
    def __init__(self, package: str) -> None:
        super().__init__(f"missing optional shadow dependency: {package}")
        self.package = package


class ThresholdMetrics(TypedDict):
    n_signals: int
    precision: float
    recall: float


class TopDecileMetrics(TypedDict):
    n: int
    mean_realized_ret: float
    mean_net_ret: float
    win_rate: float


class EvaluationMetrics(TypedDict):
    n: int
    positive_rate: float
    roc_auc: float
    pr_auc: float
    thresholds: dict[str, ThresholdMetrics]
    top_decile: TopDecileMetrics
    all_mean_net_ret: float


class ModelBenchmark(TypedDict):
    best_iteration: int
    fit_seconds: float
    single_row_ms: float
    batch_us_per_row: float
    val: EvaluationMetrics
    val_permutation_p: float


class ShadowBenchmark(TypedDict):
    models: dict[str, ModelBenchmark]
    base_score_spearman: dict[str, dict[str, float]]
    warning: str


@dataclass(frozen=True)
class TrainedShadowModel:
    __slots__ = ("name", "predict", "best_iteration", "fit_seconds")

    name: str
    predict: Callable[[pd.DataFrame], np.ndarray]
    best_iteration: int
    fit_seconds: float
