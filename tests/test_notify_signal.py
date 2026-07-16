from __future__ import annotations

import numpy as np
import pandas as pd

from src.notify_signal import (
    add_dual_mas,
    barrier_prices,
    display_symbol,
    format_signal_caption,
    signal_side,
)


def test_display_symbol_swap_and_spot() -> None:
    assert display_symbol("ETH_USDT_SWAP") == "ETHUSDT.P"
    assert display_symbol("BTC_USDT") == "BTCUSDT"


def test_barrier_long_and_short() -> None:
    tp, sl = barrier_prices(100.0, 1.0, side="LONG", tp_mult=5, sl_mult=2)
    assert tp == 105.0 and sl == 98.0
    tp_s, sl_s = barrier_prices(100.0, 1.0, side="SHORT", tp_mult=5, sl_mult=2)
    assert tp_s == 95.0 and sl_s == 102.0


def test_caption_contains_fields() -> None:
    cap = format_signal_caption(
        {
            "symbol": "ETH_USDT_SWAP",
            "side": "LONG",
            "entry_price": 2088.75,
            "atr14": 10.0,
            "score": 0.85,
            "threshold": 0.0165,
            "entry_time": "2026-07-15 12:00:00+00:00",
            "status": "open",
        }
    )
    assert "ETHUSDT.P" in cap
    assert "LONG" in cap
    assert "2088.75" in cap
    assert "止盈" in cap and "止损" in cap
    assert signal_side({"side": "SHORT"}) == "SHORT"


def test_add_dual_mas_has_sma_ema_20_60_120() -> None:
    n = 200
    close = np.linspace(100, 120, n) + np.sin(np.linspace(0, 8, n))
    frame = pd.DataFrame(
        {
            "open_time": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": 1.0,
        }
    )
    out = add_dual_mas(frame)
    for col in ("sma20", "sma60", "sma120", "ema20", "ema60", "ema120"):
        assert col in out.columns
        assert out[col].iloc[-1] == out[col].iloc[-1]  # not NaN at end
    assert pd.isna(out["sma120"].iloc[50])
    assert np.isfinite(out["sma120"].iloc[119])
