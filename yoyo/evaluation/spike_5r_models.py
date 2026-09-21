"""Causal fixed-model scoring for the approved offline Spike final-net-R>5 study.

Only the twenty signal-close fields declared by :mod:`spike_10r_search` and
the candidate's known timeframe are model inputs.  ``training_mask`` makes the
label boundary explicit: a row can train only when it was available in the
requested history and its exit bar has completely closed by ``cutoff``.  The
caller owns the outer quarterly schedule and any score-based admission rule.

The two model families here are deliberately fixed: an unweighted L2 logistic
regression and a small unweighted LightGBM classifier.  Median imputation is
always fitted from training rows alone; logistic regression additionally uses a
training-only ``StandardScaler``.  An all-missing training feature receives a
median of zero, so prediction remains defined without consulting later rows.
"""
from __future__ import annotations

import hashlib
import json
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from yoyo.evaluation.spike_10r_search import FEATURES
from yoyo.evaluation.spike_six_filter_statistics import strict_bool

LABEL_COLUMN = "label_gt5"
TIMEFRAME_COLUMN = "timeframe_min"
TIMEFRAMES = (30, 60, 240)
TIMEFRAME_FEATURES = tuple(f"timeframe_{minutes}m" for minutes in TIMEFRAMES)
MODEL_FEATURES = tuple(FEATURES) + TIMEFRAME_FEATURES
LOGISTIC = "logistic"
LIGHTGBM = "lightgbm"
MODEL_NAMES = (LOGISTIC, LIGHTGBM)
SEED = 921401


@dataclass(frozen=True)
class Preprocessing:
    """Feature fill values and the optional logistic scaling fitted on training."""

    feature_names: tuple[str, ...]
    medians: np.ndarray
    scaler: StandardScaler | None


@dataclass(frozen=True)
class FittedSpike5RModel:
    """A fixed >5R classifier with only train-derived preprocessing state."""

    name: str
    preprocessing: Preprocessing
    estimator: Any


def _utc_timestamp(value: pd.Timestamp | str) -> pd.Timestamp:
    """Normalize a cutoff to UTC and reject a date without a timezone."""
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        raise ValueError("cutoff must be timezone-aware")
    return timestamp.tz_convert("UTC")


def _required_columns(rows: pd.DataFrame, names: tuple[str, ...]) -> None:
    missing = [name for name in names if name not in rows.columns]
    if missing:
        raise ValueError(f"missing required model columns: {missing}")


def training_mask(
    rows: pd.DataFrame,
    cutoff: pd.Timestamp | str,
    history_months: int = 12,
) -> pd.Series:
    """Return rows whose final labels are mature by a causal cutoff.

    The history is ``[cutoff-history_months, cutoff)`` on ``available_at``.
    A label is available only after the exit bar closes, represented by
    ``exit_time + timeframe_min``.  Invalid, censored, future-available, and
    exit-bar-crossing candidates are always excluded.  ``entry_time < cutoff``
    retains the explicit entry-side boundary even if malformed source rows have
    an earlier availability timestamp.
    """
    if history_months <= 0:
        raise ValueError("history_months must be positive")
    _required_columns(rows, ("available_at", "entry_time", "exit_time", TIMEFRAME_COLUMN,
                             "valid_entry", "censored"))
    end = _utc_timestamp(cutoff)
    start = end - pd.DateOffset(months=history_months)
    available_at = pd.to_datetime(rows["available_at"], utc=True, errors="coerce")
    entry_time = pd.to_datetime(rows["entry_time"], utc=True, errors="coerce")
    exit_time = pd.to_datetime(rows["exit_time"], utc=True, errors="coerce")
    timeframe = pd.to_numeric(rows[TIMEFRAME_COLUMN], errors="coerce")
    label_available_at = exit_time + pd.to_timedelta(timeframe, unit="min")
    valid_entry = strict_bool(rows["valid_entry"])
    censored = strict_bool(rows["censored"])
    return (
        valid_entry
        & ~censored
        & available_at.ge(start)
        & available_at.lt(end)
        & entry_time.lt(end)
        & label_available_at.le(end)
    ).fillna(False).astype(bool)


def feature_matrix(rows: pd.DataFrame) -> np.ndarray:
    """Build the fixed 20 causal features plus a three-way timeframe one-hot.

    This intentionally selects columns by the fixed allowlist, so event IDs,
    symbol/venue identifiers, labels, outcomes, costs, and other economics
    fields cannot become model inputs.  The twenty fields are signal-close
    values documented by ``spike_10r_search.FEATURES``; ``timeframe_min`` is
    known when a candidate becomes available.  Values outside 30/60/240 minutes
    are rejected rather than silently mapped to an all-zero category.
    """
    _required_columns(rows, tuple(FEATURES) + (TIMEFRAME_COLUMN,))
    numeric = rows.loc[:, FEATURES].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    timeframe = pd.to_numeric(rows[TIMEFRAME_COLUMN], errors="coerce").to_numpy(dtype=float)
    known = np.isin(timeframe, TIMEFRAMES)
    if not known.all():
        unsupported = sorted({str(value) for value in timeframe[~known]})
        raise ValueError(f"unsupported timeframe_min values: {unsupported}")
    one_hot = np.column_stack([(timeframe == minutes).astype(float) for minutes in TIMEFRAMES])
    return np.column_stack((numeric, one_hot))


def fit_preprocessing(train: pd.DataFrame, *, standardize: bool) -> Preprocessing:
    """Fit median imputation and optional scaling from training rows only."""
    raw = feature_matrix(train)
    if not len(raw):
        raise ValueError("cannot fit preprocessing on zero training rows")
    finite = np.isfinite(raw)
    medians = np.zeros(raw.shape[1], dtype=float)
    for column in range(raw.shape[1]):
        values = raw[finite[:, column], column]
        if len(values):
            medians[column] = float(np.median(values))
    filled = np.where(finite, raw, medians)
    scaler = StandardScaler().fit(filled) if standardize else None
    return Preprocessing(feature_names=MODEL_FEATURES, medians=medians, scaler=scaler)


def transform_features(preprocessing: Preprocessing, rows: pd.DataFrame) -> np.ndarray:
    """Apply only saved train-derived preprocessing to candidates for scoring."""
    if preprocessing.feature_names != MODEL_FEATURES:
        raise ValueError("preprocessing feature contract differs from Spike 5R contract")
    raw = feature_matrix(rows)
    if raw.shape[1] != len(preprocessing.medians):
        raise ValueError("preprocessing median count differs from feature matrix")
    filled = np.where(np.isfinite(raw), raw, preprocessing.medians)
    return preprocessing.scaler.transform(filled) if preprocessing.scaler is not None else filled


def _labels(train: pd.DataFrame) -> np.ndarray:
    _required_columns(train, (LABEL_COLUMN,))
    labels = train[LABEL_COLUMN]
    if labels.isna().any():
        raise ValueError("training labels contain missing values")
    if pd.api.types.is_bool_dtype(labels):
        result = labels.to_numpy(dtype=int)
    else:
        numeric = pd.to_numeric(labels, errors="coerce")
        if numeric.isna().any() or not numeric.isin((0, 1)).all():
            raise ValueError("label_gt5 must be boolean or binary 0/1")
        result = numeric.to_numpy(dtype=int)
    if np.unique(result).size != 2:
        raise ValueError("training rows must contain both label_gt5 classes")
    return result


def fit_model(name: str, train: pd.DataFrame) -> FittedSpike5RModel:
    """Fit one fixed unweighted classifier on rows preselected by ``training_mask``.

    Callers must pass only the mature rows returned by ``training_mask``.  This
    function rejects invalid and censored source rows, but the caller supplies
    the cutoff because only its outer schedule knows the intended prediction
    quarter.
    """
    if name not in MODEL_NAMES:
        raise ValueError(f"unknown model {name!r}; expected one of {MODEL_NAMES}")
    _required_columns(train, ("valid_entry", "censored"))
    if (~strict_bool(train["valid_entry"])).any() or strict_bool(train["censored"]).any():
        raise ValueError("fit_model requires valid, uncensored mature training rows")
    labels = _labels(train)
    preprocessing = fit_preprocessing(train, standardize=name == LOGISTIC)
    matrix = transform_features(preprocessing, train)
    if name == LOGISTIC:
        estimator: Any = LogisticRegression(
            penalty="l2", C=1.0, max_iter=2000, solver="lbfgs", class_weight=None,
        ).fit(matrix, labels)
    else:
        estimator = lgb.LGBMClassifier(
            objective="binary", num_leaves=7, max_depth=3, n_estimators=150,
            learning_rate=0.03, min_child_samples=200, n_jobs=2, verbosity=-1,
            random_state=SEED, deterministic=True, force_col_wise=True,
            class_weight=None,
        ).fit(matrix, labels)
    return FittedSpike5RModel(name=name, preprocessing=preprocessing, estimator=estimator)


def predict_model(fitted: FittedSpike5RModel, rows: pd.DataFrame) -> np.ndarray:
    """Score rows without reading labels or any economic/outcome input columns."""
    if fitted.name not in MODEL_NAMES:
        raise ValueError(f"unknown fitted model {fitted.name!r}")
    if not len(rows):
        return np.empty(0, dtype=float)
    matrix = transform_features(fitted.preprocessing, rows)
    if fitted.name == LOGISTIC:
        scores = fitted.estimator.predict_proba(matrix)[:, 1]
    else:
        # A fitted sklearn wrapper returns hard classes from ``predict``;
        # native Boosters return probabilities there.
        scores = (
            fitted.estimator.predict_proba(matrix)[:, 1]
            if hasattr(fitted.estimator, "predict_proba")
            else fitted.estimator.predict(matrix)
        )
    scores = np.asarray(scores, dtype=float).reshape(-1)
    if len(scores) != len(rows) or not np.isfinite(scores).all() or (scores < 0).any() or (scores > 1).any():
        raise ValueError("model produced non-probability scores")
    return scores


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_model(fitted: FittedSpike5RModel, directory: Path | str) -> dict[str, Path]:
    """Save one model in a new self-contained directory and return its files.

    Logistic regression uses a pickle for its estimator.  LightGBM writes its
    native text booster; both save the train-only preprocessing object and a
    readable metadata contract.  A nonempty directory is refused so an older
    quarterly artifact cannot be silently overwritten.
    """
    target = Path(directory)
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"refusing to overwrite nonempty model directory: {target}")
    target.mkdir(parents=True, exist_ok=True)
    preprocessing_path = target / "preprocessing.pkl"
    with preprocessing_path.open("wb") as handle:
        pickle.dump(fitted.preprocessing, handle, protocol=pickle.HIGHEST_PROTOCOL)
    if fitted.name == LOGISTIC:
        model_path = target / "model.pkl"
        with model_path.open("wb") as handle:
            pickle.dump(fitted.estimator, handle, protocol=pickle.HIGHEST_PROTOCOL)
    elif fitted.name == LIGHTGBM:
        model_path = target / "model.txt"
        booster = fitted.estimator.booster_ if hasattr(fitted.estimator, "booster_") else fitted.estimator
        booster.save_model(str(model_path))
    else:
        raise ValueError(f"unknown fitted model {fitted.name!r}")
    metadata_path = target / "metadata.json"
    metadata_path.write_text(json.dumps({
        "format_version": 2,
        "target_r": 5,
        "label_column": LABEL_COLUMN,
        "name": fitted.name,
        "feature_names": list(fitted.preprocessing.feature_names),
        "medians": fitted.preprocessing.medians.tolist(),
        "preprocessing_file": preprocessing_path.name,
        "model_file": model_path.name,
        "files": {
            preprocessing_path.name: _sha256(preprocessing_path),
            model_path.name: _sha256(model_path),
        },
    }, indent=2, sort_keys=True) + "\n")
    return {"metadata": metadata_path, "preprocessing": preprocessing_path, "model": model_path}


def load_model(directory: Path | str) -> FittedSpike5RModel:
    """Load a model directory written by ``save_model`` and verify its files."""
    target = Path(directory)
    metadata_path = target / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    name = metadata.get("name")
    if (name not in MODEL_NAMES or metadata.get("target_r") != 5
            or metadata.get("label_column") != LABEL_COLUMN):
        raise ValueError("saved model differs from the Spike 5R target contract")
    preprocessing_path = target / metadata["preprocessing_file"]
    model_path = target / metadata["model_file"]
    for filename, expected in metadata["files"].items():
        actual = _sha256(target / filename)
        if actual != expected:
            raise ValueError(f"saved model file hash mismatch: {filename}")
    with preprocessing_path.open("rb") as handle:
        preprocessing = pickle.load(handle)
    if not isinstance(preprocessing, Preprocessing):
        raise TypeError("saved preprocessing has an unexpected type")
    if tuple(metadata["feature_names"]) != preprocessing.feature_names:
        raise ValueError("saved preprocessing feature contract drift")
    if name == LOGISTIC:
        with model_path.open("rb") as handle:
            estimator = pickle.load(handle)
    else:
        estimator = lgb.Booster(model_file=str(model_path))
    return FittedSpike5RModel(name=name, preprocessing=preprocessing, estimator=estimator)
