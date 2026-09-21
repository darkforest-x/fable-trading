"""Focused rendering geometry checks for the follow-up historical gallery."""

import numpy as np
import pandas as pd

from yoyo.datasets.ma_launch_followup50 import candle_body_polygons


def test_candle_bodies_preserve_bullish_and_bearish_open_close_height() -> None:
    polygons = candle_body_polygons(
        np.array([0, 1]), pd.Series([10.0, 12.0]), pd.Series([12.0, 10.0]), 0.001
    )
    assert [point[1] for point in polygons[0]] == [10.0, 10.0, 12.0, 12.0]
    assert [point[1] for point in polygons[1]] == [10.0, 10.0, 12.0, 12.0]
