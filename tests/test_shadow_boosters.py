from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.judgment.features import FEATURE_COLUMNS
from src.judgment.shadow_booster_types import ShadowDataError
from src.judgment.shadow_boosters import (
    equal_weight_probabilities,
    load_shadow_splits,
)
from src.judgment.train import HOLDOUT_START, PURGE_WINDOW


def test_load_shadow_splits_stops_before_holdout_and_purges_boundary(tmp_path) -> None:
    times = pd.date_range("2026-03-20", periods=220, freq="6h", tz="UTC")
    frame = pd.DataFrame(
        {
            "signal_time": times,
            "label": np.arange(len(times)) % 2,
            "realized_ret": np.linspace(-0.02, 0.03, len(times)),
            **{column: np.linspace(0.0, 1.0, len(times)) for column in FEATURE_COLUMNS},
        }
    )
    path = tmp_path / "dataset.csv"
    frame.to_csv(path, index=False)

    train, val = load_shadow_splits(path)

    assert train["signal_time"].max() < val["signal_time"].min() - PURGE_WINDOW
    assert val["signal_time"].max() < HOLDOUT_START - PURGE_WINDOW
    assert not train.empty
    assert not val.empty


def test_equal_weight_probabilities_averages_models() -> None:
    predictions = {
        "lightgbm": np.array([0.2, 0.8]),
        "catboost": np.array([0.4, 0.6]),
        "xgboost": np.array([0.6, 0.4]),
    }

    combined = equal_weight_probabilities(predictions)

    np.testing.assert_allclose(combined, np.array([0.4, 0.6]))


def test_equal_weight_probabilities_rejects_shape_mismatch() -> None:
    predictions = {
        "lightgbm": np.array([0.2, 0.8]),
        "catboost": np.array([0.4]),
    }

    with pytest.raises(ShadowDataError, match="prediction lengths differ"):
        equal_weight_probabilities(predictions)
