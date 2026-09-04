from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.optimize_btcusdtp_k1k2_intraday_preholdout import (
    build_pair_universe,
    load_config,
    select_coordinate,
)


def _featured_fixture() -> pd.DataFrame:
    n = 10
    frame = pd.DataFrame(
        {
            "open_time": pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC"),
            "open": np.full(n, 104.0),
            "high": np.full(n, 105.0),
            "low": np.full(n, 103.0),
            "close": np.full(n, 104.0),
            "atr": np.full(n, 5.0),
            "sma40_hl2": np.full(n, 100.0),
            "rope_low": np.full(n, 99.5),
            "rope_high": np.full(n, 100.5),
            "rope_mid": np.full(n, 100.0),
            "bar_range": np.full(n, 2.0),
            "lower_wick": np.full(n, 1.0),
            "upper_wick": np.full(n, 1.0),
            "body_ratio": np.zeros(n),
            "range_atr": np.full(n, 0.4),
            "volume_ratio_20": np.ones(n),
            "volume_z_96": np.zeros(n),
            "ma_shift_candle_side": np.ones(n, dtype=int),
            "ma_shift_osc": np.ones(n),
            "ma_shift_osc_delta": np.ones(n),
            "market_break_state": np.ones(n, dtype=int),
            "native_candle_side": np.ones(n, dtype=int),
            "rope_width_atr": np.full(n, 0.2),
            "rope_slope_atr_4": np.zeros(n),
            "prior_rope_width_atr_20": np.full(n, 0.2),
            "prior_range_atr_20": np.ones(n),
            "ma_up_alignment": np.ones(n),
            "ma_down_alignment": np.zeros(n),
            "market_break_up": np.zeros(n, dtype=bool),
            "market_break_down": np.zeros(n, dtype=bool),
            "atr_release_24": np.ones(n),
            "atr_pct": np.full(n, 0.05),
            "green_volume_share_20": np.ones(n),
            "segment_id": np.ones(n, dtype=int),
        }
    )
    # K1: directional body crosses SMA40 with strong close.
    frame.loc[2, ["open", "high", "low", "close"]] = [99.0, 106.0, 98.0, 105.0]
    frame.loc[2, ["bar_range", "lower_wick", "upper_wick", "body_ratio", "range_atr"]] = [
        8.0,
        1.0,
        1.0,
        0.75,
        1.6,
    ]
    # K2: body remains above SMA40 while only the lower wick touches it.
    frame.loc[4, ["open", "high", "low", "close"]] = [103.0, 105.0, 99.0, 104.0]
    frame.loc[4, ["bar_range", "lower_wick", "upper_wick", "body_ratio", "range_atr"]] = [
        6.0,
        4.0,
        1.0,
        1.0 / 6.0,
        1.2,
    ]
    return frame


def test_pair_universe_does_not_change_when_future_rows_change() -> None:
    config = load_config()
    original = _featured_fixture()
    mutated = original.copy()
    mutated.loc[6:, ["open", "high", "low", "close"]] = [10.0, 200.0, 1.0, 190.0]
    before = build_pair_universe(original, config, "15m")
    after = build_pair_universe(mutated, config, "15m")
    columns = ["direction", "k1_i", "k2_i", "gap_bars", "k1_quality"]
    pd.testing.assert_frame_equal(
        before.loc[before["k2_i"].lt(6), columns].reset_index(drop=True),
        after.loc[after["k2_i"].lt(6), columns].reset_index(drop=True),
    )
    assert ((before["direction"] == 1) & (before["k1_i"] == 2) & (before["k2_i"] == 4)).any()


def test_coordinate_move_requires_preregistered_margin() -> None:
    incumbent = {"eligible": True, "robust_score_bp": 0.0, "worst_fold_net_bp": -5.0}
    rows = [
        {
            "eligible": True,
            "robust_score_bp": 1.9,
            "worst_fold_net_bp": 5.0,
            "events": 100,
            "distance_from_inherited": 0.0,
            "value_json": "0.65",
        },
        {
            "eligible": True,
            "robust_score_bp": 3.0,
            "worst_fold_net_bp": -7.5,
            "events": 90,
            "distance_from_inherited": 0.1,
            "value_json": "0.75",
        },
    ]
    selected, reason = select_coordinate(rows, incumbent)
    assert reason == "move_by_preregistered_rule"
    assert selected is not None and selected["value_json"] == "0.75"


def test_ineligible_incumbent_does_not_waive_improvement_margin() -> None:
    incumbent = {"eligible": False, "robust_score_bp": -18.0, "worst_fold_net_bp": -24.0}
    rows = [
        {
            "eligible": True,
            "robust_score_bp": -19.0,
            "worst_fold_net_bp": -23.0,
            "events": 200,
            "distance_from_inherited": 1.0,
            "value_json": "2.0",
        }
    ]
    selected, reason = select_coordinate(rows, incumbent)
    assert selected is None
    assert reason == "retain_no_preregistered_improvement"
