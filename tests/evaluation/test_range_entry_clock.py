"""Entry causality, strict H1 timing and separation from exit-management clocks."""
import numpy as np
import pandas as pd

from yoyo.evaluation.pepe_range_exits import prepare, signals, entries
from yoyo.evaluation.range_entry_clock import entry_context, watched_entries


def candles():
    rng = np.random.default_rng(445)
    close = 100 + np.cumsum(rng.normal(0, .3, 12*24*8))
    opening = np.r_[close[0], close[:-1]]
    return pd.DataFrame(
        dict(open=opening, high=np.maximum(opening, close)+.1,
             low=np.minimum(opening, close)-.1, close=close),
        index=pd.date_range("2025-01-01T00:05Z", periods=len(close), freq="5min").as_unit("ns"))


def test_watch_refresh_requires_strictly_later_close_and_allows_many_entries():
    hour = [0, 1, 0, 1, 0, 0, -1, 0, 0]
    lower = [1, 1, 1, 1, 1, 1, -1, -1, 1]
    admitted, since = watched_entries(hour, lower)
    np.testing.assert_array_equal(admitted, [0, 0, 1, 0, 1, 1, 0, -1, 0])
    assert since[4] == 3 and since[5] == 3 and since[7] == 6


def test_15m_unchanged_and_5m_uses_only_completed_atr():
    frame = candles()
    original, default = prepare(frame), entry_context(frame)
    for key in ("signal", "atr", "hour", "observed"):
        np.testing.assert_array_equal(original[key], default[key])
    candidate = entry_context(frame, 5)
    expected_atr = pd.Series(original["atr"]).ffill().to_numpy()
    np.testing.assert_array_equal(candidate["atr"], expected_atr)
    first = np.flatnonzero(np.isfinite(original["atr"]))[0]
    assert np.isnan(candidate["atr"][:first]).all()
    np.testing.assert_array_equal(candidate["hour"], original["hour"])
    # The native preparer is unchanged: update events remain 15m-only.
    assert np.isnan(original["atr"][frame.index.minute % 15 != 0]).all()
    cut = 1733
    prefix = entry_context(frame.iloc[:cut], 5)
    for key in ("signal", "atr", "hour", "observed"):
        np.testing.assert_array_equal(prefix[key], candidate[key][:cut])


def test_off_quarter_signals_reach_candidates_and_matched_controls():
    frame = candles()
    ctx = entry_context(frame, 5)
    cfg = dict(start="2025-01-02T00:00Z",
               splits=["2025-01-04T00:00Z", "2025-01-06T00:00Z"],
               end="2025-01-09T00:00Z", windows_5m=[5], seed=99,
               volatility_edges=[.001, .002, .004, .008, .016])
    actual, control, unmatched = entries(ctx, cfg)
    assert len(actual) > 0 and len(control) == len(actual) and not unmatched
    expected = np.flatnonzero((ctx["signal"] != 0) & (frame.index >= pd.Timestamp(cfg["start"]))
                             & (frame.index < pd.Timestamp(cfg["end"]))
                             & np.isfinite(ctx["atr"]))
    np.testing.assert_array_equal(actual.entry_i.to_numpy(), expected)
    assert (frame.index[actual.entry_i].minute % 15 != 0).any()
    assert (frame.index[control.entry_i].minute % 15 != 0).any()
    raw = signals(frame).to_numpy()
    for row in actual.itertuples():
        observed = ctx["observed"][row.entry_i]
        assert 0 <= observed < row.entry_i
        assert ctx["hour"][observed] == row.side == raw[row.entry_i]
