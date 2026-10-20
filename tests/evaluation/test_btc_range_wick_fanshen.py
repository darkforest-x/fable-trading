"""Behavioral tests for causal level availability and two-sided signal timing."""
import numpy as np
import pandas as pd

from yoyo.evaluation.btc_range_wick_fanshen import (
    candidates, monday_levels, pair_arrows, touch_masks,
)
from yoyo.evaluation.pepe_range_exits import aggregate, stop_events


def frame(start="2026-10-19T04:15Z", n=6):
    return pd.DataFrame(dict(open=105., high=110., low=100., close=106.),
                        index=pd.date_range(start, periods=n, freq="15min"))


def test_monday_extrema_not_backfilled_and_reset():
    q = frame()
    q.iloc[3, q.columns.get_loc("high")] = 200
    values, _, _, _ = monday_levels(q, [.5], "America/New_York")
    assert np.isnan(values[0, 0])
    assert values[3, 0] == 105
    assert values[4, 0] == 150
    old = frame("2026-10-19T04:00Z", 4)
    values, _, _, _ = monday_levels(old, [.5], "America/New_York")
    assert np.isnan(values[1, 0])  # First Monday candle has no previous-week levels.
    assert values[2, 0] == 105


def test_directional_wick_excludes_body_and_no_wick():
    q = frame(n=2)
    prices = np.array([[101, 105.5, 109], [101, 105.5, 109]])
    q.iloc[1, q.columns.get_loc("low")] = 105
    hit = touch_masks(q, prices)
    np.testing.assert_array_equal(hit[1][0], [True, False, False])
    np.testing.assert_array_equal(hit[-1][0], [False, False, True])
    assert not hit[1][1].any()


def test_signal_after_touch_waits_for_signal_close():
    q = frame()
    prices = np.full((len(q), 1), np.nan)
    prices[1] = 101
    signals = pd.Series([0, 0, 0, 1, 0, 0], index=q.index)
    result = pair_arrows(q, signals, prices, ["0.5"], np.array(["w"]*6),
                         np.zeros(6, bool))
    assert result.iloc[0].entry_q == 3
    assert result.iloc[0].touch_q == 1


def test_touch_after_signal_waits_and_uses_first_match():
    q = frame()
    prices = np.full((len(q), 1), np.nan)
    prices[3:5] = 109
    signals = pd.Series([0, 0, -1, 0, 0, 0], index=q.index)
    args = (["1.13"], np.array(["w"]*6), np.zeros(6, bool))
    result = pair_arrows(q, signals, prices, *args)
    assert result.iloc[0].entry_q == 3
    assert result.iloc[0].touch_q == 3
    prefix = pair_arrows(q.iloc[:4], signals.iloc[:4], prices[:4],
                         args[0], args[1][:4], args[2][:4])
    pd.testing.assert_frame_equal(result, prefix)


def test_no_pair_across_new_monday_or_outside_two_bars():
    q = frame()
    prices = np.full((6, 1), np.nan)
    prices[0] = 101
    signals = pd.Series([0, 0, 1, 0, 1, 0], index=q.index)
    result = pair_arrows(q, signals, prices, ["0.5"],
                         np.array(["old", "new", "new", "new", "new", "new"]),
                         np.zeros(6, bool))
    assert result.empty


def test_nine_5m_extrema_equal_three_complete_15m_at_entry():
    index = pd.date_range("2026-10-01T00:05Z", periods=15, freq="5min")
    five = pd.DataFrame(dict(open=np.arange(15)+100., high=np.arange(15)+102.,
                            low=np.arange(15)+99., close=np.arange(15)+101.), index=index)
    q = aggregate(five, 15)
    for i in (8, 11, 14):
        for side in (-1, 1):
            source = pd.DataFrame([dict(entry_i=i, side=side, end_i=14)])
            ctx = dict(frame=five, bars=five[["open", "high", "low", "close"]].to_numpy())
            entries, _ = stop_events(ctx, source, 9, .1, .1)
            history = q.loc[:index[i]].iloc[-3:]
            extreme = history.low.min() if side == 1 else history.high.max()
            raw = (extreme-side*five.close.iloc[i]*.001)/.1
            expected = (np.floor(raw+1e-8) if side == 1 else np.ceil(raw-1e-8))*.1
            assert abs(entries.iloc[0].stop-expected) < 1e-9


def test_candidate_segmentation_identical_for_micro_and_nanosecond_indices():
    rng = np.random.default_rng(20261021)
    n = 1500
    close = 100 + np.cumsum(rng.normal(0, .2, n))
    opened = np.r_[close[0], close[:-1]]
    q = pd.DataFrame(dict(open=opened, high=np.maximum(opened, close)+.3,
                          low=np.minimum(opened, close)-.3, close=close),
                     index=pd.date_range("2026-10-19T04:15Z", periods=n, freq="15min"))
    cfg = dict(ratios=[.5,.618,.705,1.13,1.27,1.618], timezone="America/New_York",
               signal_distance_bars=2, start="2026-10-20T00:00Z",
               split="2026-10-27T00:00Z", end="2026-11-03T00:00Z",
               volatility_edges=[.001,.002,.004,.008,.016], seed=123)
    a, missing_a = candidates(q.set_axis(q.index.as_unit("us")), cfg)
    b, missing_b = candidates(q.set_axis(q.index.as_unit("ns")), cfg)
    pd.testing.assert_frame_equal(a, b)
    assert len(a) > 0 and a.family.eq("combined").any()
    assert missing_a == missing_b
