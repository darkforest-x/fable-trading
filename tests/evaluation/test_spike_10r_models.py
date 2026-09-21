"""Regression checks for causal fixed-model Spike 10R scoring."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_10r_models as models
from yoyo.evaluation.spike_10r_search import FEATURES


def _rows(count: int = 600, start: str = "2024-01-01") -> pd.DataFrame:
    available = pd.date_range(start, periods=count, freq="12h", tz="UTC")
    data: dict[str, object] = {
        "available_at": available,
        "entry_time": available + pd.Timedelta(hours=1),
        "exit_time": available + pd.Timedelta(hours=5),
        "timeframe_min": np.resize(np.array([30, 60, 240]), count),
        "valid_entry": True,
        "censored": False,
        "label_gt10": np.arange(count) % 7 == 0,
        "symbol": [f"COIN{i % 5}" for i in range(count)],
        "event_key": [f"event-{i}" for i in range(count)],
        "net_r": np.linspace(-2, 20, count),
        "net_return": np.linspace(-.05, .2, count),
    }
    axis = np.linspace(-2, 2, count)
    for position, feature in enumerate(FEATURES):
        data[feature] = np.sin(axis * (position + 1)) + axis * (position + 1) / 50
    return pd.DataFrame(data)


def test_training_mask_excludes_future_crossing_and_censored_labels():
    cutoff = pd.Timestamp("2025-10-01T00:00:00Z")
    rows = _rows(6, "2025-09-20")
    rows.loc[0, ["exit_time", "timeframe_min"]] = [pd.Timestamp("2025-09-30T23:30Z"), 30]
    rows.loc[1, ["exit_time", "timeframe_min"]] = [pd.Timestamp("2025-10-01T00:00Z"), 30]
    rows.loc[2, "censored"] = True
    rows.loc[3, "available_at"] = cutoff
    rows.loc[4, "valid_entry"] = False
    rows.loc[5, "entry_time"] = cutoff
    rows["valid_entry"] = rows["valid_entry"].map({True: "true", False: "false"})
    rows["censored"] = rows["censored"].map({True: "true", False: "false"})
    assert models.training_mask(rows, cutoff).tolist() == [True, False, False, False, False, False]


@pytest.mark.parametrize("name", models.MODEL_NAMES)
def test_future_mutation_cannot_change_earlier_training_or_scores(name):
    rows = _rows()
    cutoff = pd.Timestamp("2024-06-01T00:00:00Z")
    before_mask = models.training_mask(rows, cutoff, history_months=5)
    before = models.fit_model(name, rows.loc[before_mask])
    score_rows = rows.loc[before_mask].iloc[:40]
    before_scores = models.predict_model(before, score_rows)

    changed = rows.copy()
    future = pd.to_datetime(changed.available_at, utc=True).ge(cutoff)
    changed.loc[future, "label_gt10"] = ~changed.loc[future, "label_gt10"]
    changed.loc[future, list(FEATURES)] = 1e12
    changed.loc[future, ["net_r", "net_return", "symbol"]] = [1e12, 1e12, "FUTURE"]
    after_mask = models.training_mask(changed, cutoff, history_months=5)
    after = models.fit_model(name, changed.loc[after_mask])
    after_scores = models.predict_model(after, score_rows)

    assert after_mask.equals(before_mask)
    np.testing.assert_allclose(after_scores, before_scores, rtol=0, atol=0)


def test_feature_matrix_ignores_forbidden_columns_and_preprocessing_is_train_only():
    train = _rows(120)
    train.loc[:, "reference_risk_fraction"] = np.arange(120, dtype=float)
    train.loc[0, "reference_risk_fraction"] = np.nan
    future = _rows(5, "2026-01-01")
    future.loc[:, "reference_risk_fraction"] = 1e12
    combined = pd.concat([train, future], ignore_index=True)
    preprocessing = models.fit_preprocessing(train, standardize=True)
    feature_index = models.MODEL_FEATURES.index("reference_risk_fraction")
    assert preprocessing.medians[feature_index] == pytest.approx(np.median(np.arange(1, 120, dtype=float)))
    all_missing = train.copy()
    all_missing["volume_ratio"] = np.nan
    assert models.fit_preprocessing(all_missing, standardize=True).medians[
        models.MODEL_FEATURES.index("volume_ratio")
    ] == 0

    baseline = models.feature_matrix(train)
    changed = train.copy()
    changed.loc[:, ["symbol", "event_key", "net_r", "net_return"]] = ["LEAK", "id", 1e12, -1e12]
    changed.loc[:, "label_gt10"] = ~changed.label_gt10
    np.testing.assert_array_equal(models.feature_matrix(changed), baseline)
    # Future rows never enter preprocessing, even if their values are extreme.
    assert models.fit_preprocessing(combined.iloc[:len(train)], standardize=True).medians[feature_index] == pytest.approx(
        preprocessing.medians[feature_index]
    )


@pytest.mark.parametrize("name", models.MODEL_NAMES)
def test_scores_are_probabilities_and_serialization_roundtrips(tmp_path, name):
    train = _rows(600)
    fitted = models.fit_model(name, train)
    score_rows = train.iloc[::19].copy()
    score_rows.loc[score_rows.index[0], "volume_ratio"] = np.nan
    before = models.predict_model(fitted, score_rows)
    assert np.isfinite(before).all()
    assert ((before >= 0) & (before <= 1)).all()

    saved = models.save_model(fitted, tmp_path / name)
    assert all(path.is_file() for path in saved.values())
    restored = models.load_model(tmp_path / name)
    after = models.predict_model(restored, score_rows)
    np.testing.assert_allclose(after, before, rtol=1e-12, atol=1e-12)
    with pytest.raises(FileExistsError, match="nonempty"):
        models.save_model(fitted, tmp_path / name)
