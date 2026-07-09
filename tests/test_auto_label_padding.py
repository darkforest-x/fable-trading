"""E1: x_pad is a named single-variable constant; tighter pad yields narrower boxes."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.detection.auto_label import (
    X_PAD_PX,
    Y_PAD_FRAC,
    DenseSegment,
    segment_to_bbox,
)
from src.detection.render import ChartTransform


def _tf(n_bars: int = 50, width: int = 1280, height: int = 742) -> ChartTransform:
    left = top = 40
    return ChartTransform(
        n_bars=n_bars,
        width=width,
        height=height,
        left=left,
        top=top,
        plot_w=width - 2 * left,
        plot_h=height - 2 * top,
        price_min=100.0,
        price_max=110.0,
        candle_half_w=3,
    )


def _flat_df(n: int = 50) -> pd.DataFrame:
    # Constant MAs so y-pad is stable; only x_pad changes width.
    price = 105.0
    data = {
        "close": [price] * n,
        "sma20": [price] * n,
        "ema20": [price] * n,
        "sma60": [price] * n,
        "ema60": [price] * n,
        "sma120": [price] * n,
        "ema120": [price] * n,
    }
    return pd.DataFrame(data)


def test_e1_x_pad_default_is_six() -> None:
    assert X_PAD_PX == 6
    assert Y_PAD_FRAC == 0.35  # E1 must not touch y pad


def test_tighter_x_pad_narrows_box_width() -> None:
    df = _flat_df()
    tf = _tf()
    seg = DenseSegment(start=10, end=20)
    wide = segment_to_bbox(df, seg, tf, x_pad_px=12, y_pad_frac=Y_PAD_FRAC)
    tight = segment_to_bbox(df, seg, tf, x_pad_px=6, y_pad_frac=Y_PAD_FRAC)
    assert wide is not None and tight is not None
    _, _, w_wide, h_wide = wide
    _, _, w_tight, h_tight = tight
    assert w_tight < w_wide
    # Symmetric pad: width delta ≈ 2 * (12-6) / image_width
    expected_dw = 2 * (12 - 6) / tf.width
    assert abs((w_wide - w_tight) - expected_dw) < 1e-6
    # height unchanged when only x_pad changes
    assert abs(h_wide - h_tight) < 1e-9
