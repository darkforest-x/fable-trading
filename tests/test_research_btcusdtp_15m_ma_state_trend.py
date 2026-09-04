from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.research_btcusdtp_15m_ma_state_trend import (
    add_reference_features,
    build_raw_candidates,
    resolve_trade,
)


def synthetic_frame(rows: int = 180) -> pd.DataFrame:
    times = pd.date_range("2024-01-01", periods=rows, freq="15min", tz="UTC")
    close = np.full(rows, 100.0)
    close[130:138] = [99.9, 100.0, 100.05, 99.95, 100.0, 100.05, 99.98, 100.02]
    close[138] = 101.0
    close[139:] = np.linspace(101.2, 108.0, rows - 139)
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 0.2
    low = np.minimum(open_, close) - 0.2
    frame = pd.DataFrame(
        {
            "open_time": times,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.ones(rows),
            "atr": np.ones(rows),
            "segment_id": np.ones(rows, dtype=int),
        }
    )
    return frame


def test_reference_features_are_future_invariant() -> None:
    frame = synthetic_frame()
    before = add_reference_features(frame, "EMA30")
    mutated = frame.copy()
    mutated.loc[151:, ["open", "high", "low", "close"]] *= 9.0
    after = add_reference_features(mutated, "EMA30")
    columns = [
        "reference_ma",
        "reference_slope_atr_per_bar",
        "prior_near_ma_share",
        "prior_range_atr",
    ]
    pd.testing.assert_frame_equal(before.loc[:150, columns], after.loc[:150, columns])


def test_direct_or_coil_release_is_detected_without_k1_k2_pair() -> None:
    frame = add_reference_features(synthetic_frame(), "SMA20")
    config = {
        "lookback_bars": 8,
        "slope_lag_bars": 4,
        "direct": {
            "previous_signed_close_atr_max": 0.1,
            "current_signed_close_atr_min": 0.1,
            "signed_body_atr_min": 0.2,
            "range_atr_min": 0.65,
            "directional_close_location_min": 0.6,
            "signed_ma_slope_atr_per_bar_min": -0.04,
        },
        "rejection": {
            "current_signed_close_atr_min": 0.05,
            "signed_body_atr_min": 0.1,
            "directional_close_location_min": 0.65,
            "signed_ma_slope_atr_per_bar_min": -0.03,
            "requires_physical_ma_touch": True,
        },
        "coil": {
            "current_signed_close_atr_min": 0.15,
            "prior_breakout_atr_min": 0.05,
            "signed_body_atr_min": 0.25,
            "range_atr_min": 0.7,
            "directional_close_location_min": 0.6,
            "prior_near_ma_share_min": 0.5,
            "prior_range_atr_max": 2.75,
            "signed_ma_slope_atr_per_bar_min": -0.03,
        },
    }
    candidates = build_raw_candidates(frame, config, "all")
    hit = candidates[(candidates["signal_i"] == 138) & (candidates["direction"] == 1)]
    assert len(hit) == 1
    assert "direct" in hit.iloc[0]["signal_family"]
    assert "coil" in hit.iloc[0]["signal_family"]


def test_ma_close_exit_uses_next_open_after_completed_confirmation() -> None:
    frame = add_reference_features(synthetic_frame(), "SMA20")
    entry_i = 140
    frame.loc[145, "close"] = frame.loc[145, "reference_ma"] - 1.0
    frame.loc[146, "open"] = 104.25
    event = {
        "entry_i": entry_i,
        "direction": 1,
        "entry_price": float(frame.loc[entry_i, "open"]),
        "signal_atr": 1.0,
    }
    result = resolve_trade(
        frame,
        event,
        policy="ma_close_1",
        horizon=30,
        hard_stop_atr=20.0,
        fixed_target_atr=5.0,
        cost=0.002,
    )
    assert result["exit_i"] == 146
    assert result["exit_price"] == 104.25
    assert result["outcome"] == "ma_close_1"


def test_ma_trail_uses_only_previous_completed_ma() -> None:
    frame = add_reference_features(synthetic_frame(), "SMA20")
    event = {
        "entry_i": 140,
        "direction": 1,
        "entry_price": float(frame.loc[140, "open"]),
        "signal_atr": 1.0,
    }
    first = resolve_trade(
        frame,
        event,
        policy="ma_trail_0_5",
        horizon=25,
        hard_stop_atr=20.0,
        fixed_target_atr=5.0,
        cost=0.002,
    )
    mutated = frame.copy()
    mutated.loc[int(first["exit_i"]), "reference_ma"] *= 100.0
    second = resolve_trade(
        mutated,
        event,
        policy="ma_trail_0_5",
        horizon=25,
        hard_stop_atr=20.0,
        fixed_target_atr=5.0,
        cost=0.002,
    )
    assert second["exit_i"] == first["exit_i"]
    assert second["exit_price"] == first["exit_price"]
