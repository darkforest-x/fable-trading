"""Causal one-bar extrema, outward rounding, and preserved native indices."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.range_entry_bar_stop import replace_stops


@pytest.mark.parametrize("side,expected", [(1, 99.95), (-1, 100.26)])
def test_entry_bar_stop_uses_only_signal_bar_and_entry_price_buffer(side, expected):
    frame = pd.DataFrame([
        [100, 150, 50, 100], [100, 100.205, 100.005, 100.1], [100, 200, 10, 110],
    ], columns=["open", "high", "low", "close"])
    base = pd.DataFrame([dict(trade_id="test", entry_i=9, entry_i_5m=1,
                              end_i=14, end_i_5m=2, side=side)])
    got = replace_stops(frame, base)
    assert got.stop.iloc[0] == pytest.approx(expected)
    assert got.entry_i.iloc[0] == 9 and got.end_i.iloc[0] == 14
    assert got.risk.iloc[0] == pytest.approx(side*(100.1-expected))
    prefix = replace_stops(frame.iloc[:2], base)
    pd.testing.assert_frame_equal(got, prefix)


@pytest.mark.parametrize("side", [1, -1])
def test_one_bar_stop_is_never_wider_than_five_bars(side):
    frame = pd.DataFrame([[100, 105, 90, 100]]*4 + [[100, 101, 99, 100]],
                         columns=["open", "high", "low", "close"])
    base = pd.DataFrame([dict(trade_id="test", entry_i=24, entry_i_5m=4,
                              end_i=29, end_i_5m=5, side=side)])
    one, five = replace_stops(frame, base, 1), replace_stops(frame, base, 5)
    assert one.risk.iloc[0] < five.risk.iloc[0]
    np.testing.assert_array_equal(one.entry, five.entry)
