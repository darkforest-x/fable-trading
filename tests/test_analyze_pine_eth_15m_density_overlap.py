"""Tests for the Pine/project density semantic-overlap audit."""
from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.analyze_pine_eth_15m_density_overlap import (
    circular_overlap_null,
    component_passes,
)


def test_circular_overlap_null_enumerates_every_shift_and_keeps_sides() -> None:
    signal_long = np.asarray([1, 0, 0, 0], dtype=np.int8)
    signal_short = np.asarray([0, 0, 1, 0], dtype=np.int8)
    eligible_long = np.asarray([1, 0, 0, 0], dtype=np.int8)
    eligible_short = np.asarray([0, 0, 1, 0], dtype=np.int8)
    result = circular_overlap_null(
        signal_long, signal_short, eligible_long, eligible_short
    )
    assert result["signals"] == 2
    assert result["observed_overlap"] == 2
    assert result["exact_shifts"] == 4
    assert result["exact_circular_shift_p_enrichment"] == 0.25


def test_component_passes_uses_short_directional_columns() -> None:
    frame = pd.DataFrame(
        {
            "fast_spread": [0.001],
            "full_spread": [0.002],
            "fast_slow_gap": [0.001],
            "full_ratio_min48": [1.0],
            "pre_range48": [0.01],
            "pre_range168": [0.02],
            "drawdown24": [0.5],
            "runup24": [0.001],
            "ext_up": [0.5],
            "ext_down": [0.001],
            "order_score": [0],
            "down_order_score": [4],
            "slow_slope_abs": [0.0001],
            "zero_volume96": [0.0],
            "volume_ratio": [1.0],
        }
    )
    thresholds = {
        "fast_spread_max": 0.0028,
        "full_spread_max": 0.0055,
        "fast_slow_gap_max": 0.0035,
        "full_ratio_min48_max": 1.45,
        "pre_range48_max": 0.032,
        "pre_range168_max": 0.075,
        "drawdown24_max": 0.007,
        "ext_up_min": -0.0015,
        "ext_up_max": 0.0075,
        "order_score_min": 3,
        "slow_slope_abs_max": 0.0009,
        "zero_volume96_max": 0.02,
        "volume_ratio_min": 0.7,
    }
    short = component_passes(frame, side="short", thresholds=thresholds)
    long = component_passes(frame, side="long", thresholds=thresholds)
    assert short.iloc[0].all()
    assert not long.loc[0, "side_range24"]
    assert not long.loc[0, "side_extension"]
    assert not long.loc[0, "side_order"]
