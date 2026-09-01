from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scripts.render_15m_ma_launch_l2_side_split_selected20 import Selected20RenderError
from scripts.render_15m_ma_launch_l2_side_split_selected20_l1_inputs import (
    render_exact_l1_views,
)
from scripts.research_15m_ma_launch_l2_global_context import pixel_sha256
from yoyo.layers.l1_detection.data import add_mas
from yoyo.layers.l1_detection.render import render_chart


def synthetic_frame() -> pd.DataFrame:
    bars = np.arange(180, dtype=float)
    close = 100.0 + bars * 0.03 + np.sin(bars / 6.0) * 0.4
    open_ = close + np.cos(bars / 5.0) * 0.08
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2026-01-01", periods=len(bars), freq="15min", tz="UTC"),
            "open": open_,
            "high": np.maximum(open_, close) + 0.15,
            "low": np.minimum(open_, close) - 0.15,
            "close": close,
            "volume": np.full(len(bars), 1000.0),
        }
    )


def frozen_row(frame: pd.DataFrame, *, window_len: int = 18) -> dict[str, object]:
    end_i = 150
    start_i = end_i - window_len + 1
    enriched = add_mas(frame)
    raw, _ = render_chart(enriched.iloc[start_i : end_i + 1], out_path=None)
    return {
        "episode_id": "fixture-event",
        "window_start_i": start_i,
        "window_end_i": end_i,
        "feature_bar_i": end_i,
        "window_len": window_len,
        "input_pixel_sha256": pixel_sha256(raw),
        "prediction_cx_norm": 0.62,
        "prediction_cy_norm": 0.55,
        "prediction_w_norm": 0.22,
        "prediction_h_norm": 0.30,
        "class_id": 0,
    }


@pytest.mark.parametrize("window_len", [18, 19])
def test_render_exact_l1_views_preserves_raw_pixels(window_len: int) -> None:
    frame = synthetic_frame()
    row = frozen_row(frame, window_len=window_len)

    raw, detected = render_exact_l1_views(row, frame)

    assert raw.shape == (742, 1280, 3)
    assert pixel_sha256(raw) == row["input_pixel_sha256"]
    assert not np.array_equal(raw, detected)


def test_render_exact_l1_views_rejects_non_frozen_window_length() -> None:
    frame = synthetic_frame()
    row = frozen_row(frame, window_len=20)

    with pytest.raises(Selected20RenderError, match="unexpected L1 window length"):
        render_exact_l1_views(row, frame)
