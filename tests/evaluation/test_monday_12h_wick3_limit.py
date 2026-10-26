"""Wick-only 12H touch semantics for the owner 2026-10-27 three-wick limit study."""
import numpy as np
import pandas as pd

from yoyo.evaluation.monday_12h_wick3_limit import candles12, touched


def frame(lows, opens=None, closes=None):
    """One 12H candle per entry: 144 flat 5m bars at 100 with one wick bar."""
    rows = []
    for k, low in enumerate(lows):
        for b in range(144):
            o = (opens or [100]*len(lows))[k] if b == 0 else 100.
            c = (closes or [100]*len(lows))[k] if b == 143 else 100.
            rows.append((o, max(o, c, 100.), low if b == 70 else min(o, c, 100.), c))
    index = pd.date_range('2026-08-11T00:00Z', periods=len(rows), freq='5min')
    return pd.DataFrame(rows, columns=['open', 'high', 'low', 'close'], index=index)


def test_wick_counts_but_body_touch_does_not():
    f = frame([95, 95], closes=[100, 96])
    c = candles12(f)
    lo, hi = f.low.to_numpy(), f.high.to_numpy()
    assert touched(c, lo, hi, 0, 0, len(f), 96., 1)
    # Second candle closes exactly at the level: its body reaches it.
    assert not touched(c, lo, hi, 1, 0, len(f), 96., 1)


def test_only_window_part_of_straddling_candle_can_touch():
    f = frame([95])
    c = candles12(f)
    lo, hi = f.low.to_numpy(), f.high.to_numpy()
    assert not touched(c, lo, hi, 0, 0, 70, 96., 1)
    assert touched(c, lo, hi, 0, 0, 71, 96., 1)
    assert np.array_equal(c['complete'], [True])
