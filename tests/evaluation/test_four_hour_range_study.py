"""Check economic units, causal matching and source conflict boundaries."""
import numpy as np
import pandas as pd
import pytest

from yoyo.data.four_hour_range_source import merge
from yoyo.evaluation.four_hour_range import prepare
from yoyo.evaluation.four_hour_range_study import describe, matched_controls, control_summary


def test_net_metrics_do_not_confuse_r_with_account_returns():
    t = pd.DataFrame(dict(
        entry_time=pd.to_datetime(['2025-01-02T12:00Z', '2025-02-02T12:00Z'], utc=True),
        gross_r=[2., -1.], net_r=[1.8, -1.1], gross_return=[.02, -.02],
        net_return=[.018, -.022], risk_pct=[.01, .02], holding_bars=[2, 4],
        dual_touch=[False, True], censored=[False, False], reason=['target', 'stop']))
    s = describe(t)
    assert s['net_r'] == pytest.approx(.7)
    assert s['net_bp_mean'] == pytest.approx(-20)
    assert s['profit_factor_r'] == pytest.approx(1.8 / 1.1)
    assert s['profit_factor_bp'] == pytest.approx(180 / 220)
    assert s['max_closed_drawdown_r'] == pytest.approx(1.1)
    assert s['cost_r'] == pytest.approx(.3)


def test_matching_uses_closed_features_and_same_block_without_resampling():
    ix = pd.date_range('2025-01-01T05:00Z', periods=38 * 288, freq='5min')
    values = 100 + .1 * np.sin(np.arange(len(ix)) / 20)
    f = pd.DataFrame(dict(open=values, high=values + .1, low=values - .1,
                          close=values, volume=np.ones(len(ix))), index=ix)
    ctx = prepare(f)
    start, split, end = pd.Timestamp('2025-02-01T05:00Z'), pd.Timestamp('2025-02-04T05:00Z'), ix[-1] + pd.Timedelta(minutes=5)
    ei = ix.get_loc(pd.Timestamp('2025-02-03T15:00Z'))
    trades = pd.DataFrame([dict(trade_id=1, entry_i=ei, entry_time=ix[ei], side=-1,
                                risk_pct=.01, net_r=.2, net_return=.002)])
    controls, cover = matched_controls(ctx, trades, start, split, end)
    assert cover.selected_controls.iloc[0] == 13  # Sparse matching cell: never duplicate to reach 50.
    assert controls.control_entry_i.nunique() == 13
    assert ei not in controls.control_entry_i.to_list()
    assert (controls.control_entry_time < split).all()
    assert (controls.control_entry_time.dt.tz_convert('America/New_York').dt.hour == 10).all()
    assert (ctx['vol_bucket'][controls.control_entry_i.to_numpy() - 1] == ctx['vol_bucket'][ei - 1]).all()
    assert not controls.control_censored.any()
    s = control_summary(controls)
    assert s['matched_trades'] == 1 and s['controls'] == 13
    assert s['month_sign_permutation_p_greater'] is None  # One independent month.
    # The ready bucket, unlike warmup -1 buckets, must be invariant to the future.
    later = f.copy()
    later.iloc[ei + 1:, later.columns.get_loc('high')] = 200
    changed = prepare(later)
    assert ctx['vol_bucket'][ei - 1] >= 0
    np.testing.assert_array_equal(ctx['vol_bucket'][:ei + 1], changed['vol_bucket'][:ei + 1])


def test_seed_merge_rejects_price_conflicts_but_logs_volume_roundoff():
    a = pd.DataFrame(dict(open=[100.], high=[101.], low=[99.], close=[100.], volume=[123456.789]),
                     index=pd.DatetimeIndex(['2025-01-01'], tz='UTC'))
    b = a.copy()
    b.volume += 1e-10
    assert len(merge([a, b])) == 1
    b.close += 1e-10
    with pytest.raises(ValueError, match='OHLC'):
        merge([a, b])
    b = a.copy()
    b.volume += .001
    with pytest.raises(ValueError, match='volume'):
        merge([a, b])
