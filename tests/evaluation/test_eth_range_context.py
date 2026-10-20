"""Behavioral boundaries for causal, admission-only ETH context research."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.eth_range_context import (
    context_features, attach_context, masks, union_masks, match_candidates, select_development,
    evaluate_admissions,
)


def candles(start="2024-10-20", end="2024-11-12"):
    index = pd.date_range(start, end, freq="5min", tz="UTC", inclusive="right").as_unit("ns")
    v = 100+np.sin(np.arange(len(index))/17)*3+np.arange(len(index))*.001
    return pd.DataFrame(dict(open=v, high=v+1, low=v-1, close=v), index=index)


@pytest.mark.parametrize("day,expected_hours", [("2024-11-03", 25), ("2025-03-09", 23)])
def test_previous_day_uses_complete_ny_dst_day(day, expected_hours):
    start = pd.Timestamp(day).tz_localize("America/New_York").tz_convert("UTC")
    end = (pd.Timestamp(day)+pd.Timedelta(days=1)).tz_localize("America/New_York").tz_convert("UTC")
    assert (end-start)/pd.Timedelta(hours=1) == expected_hours
    bars = candles(str((start-pd.Timedelta(days=2)).date()), str((end+pd.Timedelta(days=1)).date()))
    bars.loc[(bars.index > start) & (bars.index <= end), "high"] = 999
    f = context_features(bars)
    assert f.loc[end, "day_hi"] == 999
    assert f.loc[end-pd.Timedelta(minutes=5), "day_hi"] != 999
    assert f.loc[end, "day_asof"] == end.value


def test_monday_develops_without_future_high_then_freezes_and_resets():
    bars = candles()
    monday = pd.Timestamp("2024-10-28 04:00", tz="UTC")
    morning, evening = monday+pd.Timedelta(hours=1), monday+pd.Timedelta(hours=20)
    bars.loc[evening, "high"] = 999
    f = context_features(bars)
    assert f.loc[morning, "mon_hi"] < 999
    assert f.loc[evening, "mon_hi"] == 999
    assert f.loc[monday+pd.Timedelta(days=6), "mon_hi"] == 999
    reset = pd.Timestamp("2024-11-04 05:05", tz="UTC")
    assert f.loc[reset, "mon_hi"] == bars.loc[reset, "high"]


def test_higher_features_only_change_at_completed_four_hour_close_and_prefix():
    bars = candles()
    f = context_features(bars)
    changes = f.h4_momentum.ne(f.h4_momentum.shift()) & f.h4_momentum.notna()
    assert (f.index[changes].minute == 0).all()
    assert (f.index[changes].hour % 4 == 0).all()
    cut = 3901
    pd.testing.assert_frame_equal(context_features(bars.iloc[:cut]), f.iloc[:cut], check_freq=False)
    t = pd.Timestamp("2024-11-01 12:00", tz="UTC")
    assert f.loc[t-pd.Timedelta(minutes=5), "h4_asof"] == (t-pd.Timedelta(hours=4)).value
    assert f.loc[t, "h4_asof"] == t.value


def test_missing_bars_rejected_and_first_incomplete_day_not_backfilled():
    bars = candles()
    with pytest.raises(ValueError, match="missing"):
        context_features(bars.drop(bars.index[500]))
    short = bars.loc["2024-10-23 12:00":"2024-10-24 03:55"]
    assert context_features(short).day_hi.isna().all()


def test_masks_are_symmetric_and_do_not_use_outcomes():
    rows = pd.DataFrame({
        "observable": [True]*4+[False], "side": [1, -1, 1, -1, 1],
        "day_u": [.2, .2, 1.1, 1.1, .2], "mon_u": [.3, .3, 1.2, 1.2, .3],
        "h4_arrow": [1, -1, -1, 1, 1], "h4_momentum": [-1, 1, 1, -1, 1],
        "net_r": [100, -10, 0, 1, 500], "peak_r": [200]*5,
    })
    a = masks(rows)
    assert len(a) == 15
    np.testing.assert_array_equal(a["day_edge"], [True, True, False, False, False])
    np.testing.assert_array_equal(a["mon_breakout__h4_momentum"], [False, False, True, True, False])
    b = masks(rows.assign(net_r=-999, peak_r=-999))
    for key in a:
        np.testing.assert_array_equal(a[key], b[key])


def test_matching_uses_own_filter_eligibility_and_never_reuses_controls():
    s = pd.DataFrame(dict(segment=[0]*3, week=[10]*3, side=[1]*3, bucket=[2]*3,
                          pair_id=[0, 1, 2], entry_i=[100, 200, 300],
                          trade_id=["s0", "s1", "s2"], net_r=[0, 100, -1]))
    r = pd.DataFrame(dict(segment=[0]*3, week=[10]*3, side=[1, 1, -1], bucket=[2]*3,
                          pair_id=[0, 8, 2], entry_i=[190, 205, 290],
                          trade_id=["r0", "r8", "r2"], net_r=[99, -50, 1000]), index=[10, 11, 12])
    pairs = match_candidates(s, r)
    assert pairs == [(0, 10), (1, 11)]  # original pairing first; incompatible direction rejected
    assert len({y for _, y in pairs}) == len(pairs)
    assert pairs == match_candidates(s.assign(net_r=-np.arange(3)), r.assign(net_r=-999))
    assert match_candidates(s, r.iloc[:1]) == [(0, 10)]


def test_selection_does_not_read_later_returns_or_sacrifice_tail_floor():
    table = pd.DataFrame([
        dict(arm="baseline", segment=0, n=900, watches=150, retain_10r=1, retain_20r=1, mean_net_bp=-15),
        dict(arm="valid", segment=0, n=300, watches=70, retain_10r=.8, retain_20r=.75, mean_net_bp=-5),
        dict(arm="sacrifices_winners", segment=0, n=300, watches=70, retain_10r=.2, retain_20r=.2, mean_net_bp=100),
        dict(arm="later_winner", segment=1, n=300, watches=70, retain_10r=.9, retain_20r=.9, mean_net_bp=999),
    ])
    cfg = dict(minimum_development_trades=100, minimum_development_watches=20, minimum_winner_retention=.7)
    assert select_development(table, cfg)["selected"] == "valid"
    table.loc[table.segment > 0, "mean_net_bp"] = -999
    assert select_development(table, cfg)["selected"] == "valid"
    table.loc[table.arm == "valid", "retain_20r"] = .6
    assert select_development(table, cfg)["selected"] == "baseline"


def test_side_oriented_range_position_and_input_outcomes_preserved():
    f = pd.DataFrame(dict(day_hi=[110]*2, day_lo=[90]*2, mon_hi=[110]*2, mon_lo=[90]*2,
                          h4_arrow=[1, -1], h4_momentum=[1, -1], h1_watch=[1, 2]))
    trades = pd.DataFrame(dict(entry_i=[0, 1], entry=[94, 106], side=[1, -1], net_r=[-1, 20]))
    result = attach_context(trades, f)
    np.testing.assert_allclose(result.day_u, [.2, .2])
    pd.testing.assert_series_equal(result.net_r, trades.net_r)


def test_exploratory_unions_keep_either_support_but_not_unknown_context():
    rows = pd.DataFrame(dict(observable=[True, True, True, False], side=[1]*4,
                             day_u=[.1, .7, .7, .1], mon_u=[.1, .7, .7, .1],
                             h4_arrow=[-1, 1, -1, 1], h4_momentum=[-1, 1, -1, 1]))
    values = union_masks(rows)
    assert len(values) == 8
    np.testing.assert_array_equal(values["day_edge__or__h4_arrow"], [True, True, False, False])
    assert set(values).isdisjoint(masks(rows))


def test_shared_admission_evaluator_keeps_all_arms_and_time_segments():
    rows = pd.DataFrame(dict(
        population=["signal", "random", "signal", "random"], segment=[0, 0, 1, 1],
        week=[10, 10, 20, 20], side=[1]*4, bucket=[1]*4, pair_id=[0, 0, 1, 1],
        entry_i=[10, 20, 30, 40], trade_id=["s0", "r0", "s1", "r1"], h1_watch=[1, 1, 2, 2],
        net_r=[20, -1, -1, -1], gross_r=[21, 0, 0, 0], peak_r=[22, 0, 0, 0],
        net_notional_bp=[200, -10, -10, -10], reason=[1]*4))
    cfg = dict(seed=1, bootstrap_samples=10, sign_flips=10)
    records, inference, pairs, decisions = evaluate_admissions(
        rows, rows[rows.population == "signal"],
        {"both": np.ones(4, dtype=bool), "early": np.array([True, True, False, False])}, cfg)
    assert len(records) == len(inference) == 10
    assert len(pairs) == len(decisions) == 2
    full = next(r for r in records if r["arm"] == "both" and r["segment"] == -1)
    assert full["net_r"] == 19 and full["gross_20r"] == 1
    assert full["matched_excess_bp"] == 105
