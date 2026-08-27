from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yoyo.datasets.ma_launch_density_core_box_review import (
    CORE_BARS,
    DensityCoreReviewError,
    _render_html,
    density_core_box,
)
from yoyo.datasets.ma_rope_filter import SIX_MA_COLUMNS
from yoyo.layers.l1_detection.render import make_chart_transform


def frame() -> pd.DataFrame:
    close = np.linspace(100.0, 101.0, 20)
    out = pd.DataFrame(
        {
            "open": close - 0.08,
            "high": close + 0.25,
            "low": close - 0.28,
            "close": close,
            "volume": np.ones(20),
        }
    )
    for index, column in enumerate(SIX_MA_COLUMNS):
        out[column] = close + (index - 2.5) * 0.01
    return out


def test_density_core_box_contains_exact_five_bar_wicks_and_mas() -> None:
    data = frame()
    transform = make_chart_transform(data)
    box = density_core_box(transform, data, start_local=8, end_local=12)
    assert box["core_bars"] == CORE_BARS == 5
    assert box["confirmation_bars"] == 0
    assert box["contains_core_wicks_and_six_mas"] is True
    expected_x0 = transform.x_at(8) - transform.candle_half_w - 2
    expected_x1 = transform.x_at(12) + transform.candle_half_w + 2
    assert box["x0"] == pytest.approx(expected_x0)
    assert box["x1"] == pytest.approx(expected_x1)


def test_post_core_mutation_cannot_move_any_coordinate() -> None:
    data = frame()
    transform = make_chart_transform(data)
    baseline = density_core_box(transform, data, start_local=8, end_local=12)
    changed = data.copy()
    changed.loc[13:, ["high", "low", *SIX_MA_COLUMNS]] *= 100.0
    mutated = density_core_box(transform, changed, start_local=8, end_local=12)
    for key in ("x0", "y0", "x1", "y1"):
        assert mutated[key] == pytest.approx(baseline[key])


def test_core_length_other_than_five_fails_closed() -> None:
    data = frame()
    with pytest.raises(DensityCoreReviewError, match="exactly five"):
        density_core_box(make_chart_transform(data), data, start_local=9, end_local=12)


def test_review_html_points_into_public_images_subdirectory() -> None:
    html = _render_html(
        [
            {
                "symbol": "BTC_USDT_SWAP",
                "direction": "LONG",
                "anchor_time": "2026-05-01T00:00:00Z",
                "core_start_offset": -7,
                "core_end_offset": -3,
                "box": {"source_width_px": 311.0},
                "image_path": "experiments/x/results/public/images/01_sample.png",
                "sample_id": "sample",
            }
        ],
        "abc",
    )
    assert "src='images/01_sample.png'" in html
