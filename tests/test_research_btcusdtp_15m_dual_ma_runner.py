from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.research_btcusdtp_15m_dual_ma_runner import (
    accept_with_policy,
    add_dual_references,
    resolve_runner,
)
from scripts.research_btcusdtp_15m_ma_state_trend import build_raw_candidates


def frame(rows: int = 220) -> pd.DataFrame:
    times = pd.date_range("2023-01-01", periods=rows, freq="15min", tz="UTC")
    close = np.full(rows, 100.0)
    for start in (130, 150, 175):
        close[start - 4 : start] = [99.9, 100.0, 99.95, 100.05]
        close[start] = 101.0
        close[start + 1 : start + 5] = np.linspace(101.2, 102.0, 4)
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 0.2
    low = np.minimum(open_, close) - 0.2
    return pd.DataFrame(
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


SIGNAL = {
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


def test_dual_references_do_not_cross_future_or_overwrite_trigger() -> None:
    base = frame()
    first = add_dual_references(base, "EMA20", "SMA60")
    changed = base.copy()
    changed.loc[181:, ["open", "high", "low", "close"]] *= 5.0
    second = add_dual_references(changed, "EMA20", "SMA60")
    pd.testing.assert_series_equal(
        first.loc[:180, "reference_ma"], second.loc[:180, "reference_ma"]
    )
    pd.testing.assert_series_equal(first.loc[:180, "trend_ma"], second.loc[:180, "trend_ma"])
    assert not np.allclose(first["reference_ma"], first["trend_ma"], equal_nan=True)


def test_state_reset_requires_three_false_bars_before_rearming() -> None:
    enriched = add_dual_references(frame(), "EMA20", "SMA60")
    raw = build_raw_candidates(enriched, SIGNAL, "all")
    first = raw.loc[raw["signal_i"].eq(130)].iloc[0].copy()
    second = raw.loc[raw["signal_i"].eq(150)].iloc[0].copy()
    consecutive = []
    for template, signal_i in ((first, 130), (first, 131), (first, 132), (second, 150)):
        row = template.copy()
        row["signal_i"] = signal_i
        row["signal_time"] = enriched.loc[signal_i, "open_time"]
        consecutive.append(row)
    candidates = pd.DataFrame(consecutive)
    accepted = accept_with_policy(candidates, enriched, "state_reset3")
    long_indices = accepted.loc[accepted["direction"].eq(1), "signal_i"].astype(int).tolist()
    assert long_indices == [130, 150]


def test_delayed_ma_close_cannot_exit_before_one_atr_arm() -> None:
    enriched = add_dual_references(frame(), "EMA20", "SMA60")
    entry_i = 131
    enriched.loc[entry_i : entry_i + 3, "close"] = 99.0
    enriched.loc[entry_i, "low"] = 97.5
    event = {
        "entry_i": entry_i,
        "entry_price": 100.0,
        "direction": 1,
        "signal_atr": 1.0,
    }
    result = resolve_runner(enriched, event, "ma_close2_after_1atr", 20, 2.0, 5.0)
    assert result["outcome"] == "hard_stop"
    assert result["runner_armed"] is False


def test_half_runner_is_mean_of_two_legs_before_single_cost() -> None:
    enriched = add_dual_references(frame(), "EMA20", "SMA60")
    event = {
        "entry_i": 131,
        "entry_price": float(enriched.loc[131, "open"]),
        "direction": 1,
        "signal_atr": 1.0,
    }
    split = resolve_runner(
        enriched,
        event,
        "half_fixed5_half_ma_close2_after_1atr",
        50,
        2.0,
        5.0,
    )
    fixed = resolve_runner(enriched, event, "fixed_5atr", 50, 2.0, 5.0)
    runner = resolve_runner(enriched, event, "ma_close2_after_1atr", 50, 2.0, 5.0)
    assert np.isclose(split["gross_return"], 0.5 * (fixed["gross_return"] + runner["gross_return"]))
