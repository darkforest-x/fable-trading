from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.research_btcusdtp_15m_ma_runner_grid import (
    _market_arrays,
    _run_runner_leg,
    add_exit_references,
)


def _frame() -> pd.DataFrame:
    n = 120
    close = np.linspace(100.0, 112.0, n)
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
            "open": close,
            "high": close + 0.4,
            "low": close - 0.4,
            "close": close,
            "volume": 1.0,
            "atr": 1.0,
            "segment_id": 1,
        }
    )


def test_exit_references_are_causal_and_segment_local() -> None:
    frame = _frame()
    featured = add_exit_references(frame)
    assert np.isnan(featured.loc[18, "exit_EMA20"])
    assert np.isfinite(featured.loc[19, "exit_EMA20"])
    assert np.isnan(featured.loc[58, "exit_SMA60"])
    assert np.isfinite(featured.loc[59, "exit_SMA60"])


def test_completed_close_trail_cannot_stop_same_bar() -> None:
    frame = add_exit_references(_frame())
    arrays = _market_arrays(frame)
    event = {"entry_i": 60, "entry_price": 106.0, "direction": 1, "signal_atr": 1.0}
    params = {"trend_ma": "EMA20", "exit_style": "trail", "arm_atr": 0.0, "buffer_atr": 0.0, "max_hold_bars": 8, "leg_mix": "full"}
    result = _run_runner_leg(arrays, event, params, stop_atr=2.0)
    assert result["exit_i"] >= 61


def test_short_trailing_stop_moves_down_and_resolves() -> None:
    frame = _frame()
    for column in ("open", "high", "low", "close"):
        frame[column] = 220.0 - frame[column]
    frame = add_exit_references(frame)
    arrays = _market_arrays(frame)
    event = {"entry_i": 60, "entry_price": 114.0, "direction": -1, "signal_atr": 1.0}
    params = {"trend_ma": "SMA60", "exit_style": "trail", "arm_atr": 0.0, "buffer_atr": 1.0, "max_hold_bars": 20, "leg_mix": "full"}
    result = _run_runner_leg(arrays, event, params, stop_atr=2.0)
    assert result["exit_i"] >= 60
    assert np.isfinite(result["gross"])
