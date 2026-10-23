"""Case4 close-only confirmations and NY-anchor/UTC4h boundary causality."""
import numpy as np
import pandas as pd
from yoyo.evaluation import monday_4h_close_hold as m

CFG = {'split': '2023-01-01T00:00:00Z', 'seed': 20261023,
       'controls_per_signal': 3, 'first_touch_only': True}


def fixture():
    ix = pd.date_range('2026-10-19T04:00Z', periods=32, freq='4h')
    h = pd.DataFrame(np.tile([105., 110., 100., 105.], (len(ix), 1)), columns=m.old.OHLC, index=ix)
    h.iloc[8] = [96., 97., 93., 95.]
    h.iloc[9:12] = [[92., 98., 91., 95.], [94., 98., 92., 97.], [97., 99., 94., 98.]]
    h.iloc[12] = [98., 99., 97., 98.]
    h['bars'] = 48; h['minutes'] = 240
    f = pd.DataFrame(np.repeat(h[m.old.OHLC].to_numpy(), 48, axis=0), columns=m.old.OHLC,
                     index=pd.date_range(ix[0], periods=len(h)*48, freq='5min'))
    f['count'] = 5
    return h, f


def test_confirmation_opens_and_wicks_may_cross_pin_extreme():
    h, _ = fixture()
    pin, confirm = m.pattern_mask(h, 93.82, 1)
    assert pin.iloc[8] and confirm.iloc[8]
    assert h.open.iloc[9] < 93.82 and h.low.iloc[9] < h.low.iloc[8]


def test_confirmation_close_on_level_allowed_beyond_rejected():
    h, _ = fixture(); h.loc[h.index[11], 'close'] = 93.82
    assert m.pattern_mask(h, 93.82, 1)[1].iloc[8]
    h.loc[h.index[11], 'close'] = 93.81
    assert not m.pattern_mask(h, 93.82, 1)[1].iloc[8]
    h, _ = fixture(); h.loc[h.index[8], 'open'] = 93.82
    assert not m.pattern_mask(h, 93.82, 1)[0].iloc[8]


def test_confirmations_complete_and_consecutive():
    h, _ = fixture(); h.loc[h.index[10], 'minutes'] = 239
    assert not m.pattern_mask(h, 93.82, 1)[1].iloc[8]
    h, _ = fixture(); h = h.drop(h.index[10])
    assert not m.pattern_mask(h, 93.82, 1)[1].iloc[8]


def test_entry_waits_sixteen_hours_and_future_prices_cannot_change_signal():
    _, f = fixture(); a, _ = m.candidates(f, 'BTCUSDT', CFG)
    s = a[a.population.eq('signal')].iloc[0]
    assert s.entry_i == 12*48 and s.entry == 98 and s.stop == 93 and s.target_price == 100
    assert pd.Timestamp(s.entry_time) == pd.Timestamp(s.pin_time)+pd.Timedelta(hours=16)
    changed = f.copy(); changed.iloc[12*48+1:, :4] = [900., 1000., 800., 950.]
    b, _ = m.candidates(changed, 'BTCUSDT', CFG)
    pd.testing.assert_frame_equal(a[a.population.eq('signal')].reset_index(drop=True),
                                  b[b.population.eq('signal')].reset_index(drop=True))
    assert m.candidates(f.iloc[:12*48], 'BTCUSDT', CFG)[0].empty


def test_bad_first_touch_and_missing_prefix_cannot_be_replaced():
    _, f = fixture(); f.iloc[7*48:8*48, :4] = [92., 97., 91., 95.]
    assert m.candidates(f, 'BTCUSDT', CFG)[0].empty
    a, _ = m.candidates(f, 'BTCUSDT', {**CFG, 'first_touch_only': False})
    assert len(a[a.population.eq('signal')]) == 1
    _, f = fixture(); f = f.drop(f.index[6*48])
    a, d = m.candidates(f, 'BTCUSDT', CFG)
    assert a.empty and d['funnel']['1']['unknown_prefix'] == 1


def test_short_mirror_and_spot_conflict_exclusion():
    _, f = fixture(); other = f.copy()
    other['open'] = 210-f.open; other['close'] = 210-f.close
    other['high'] = 210-f.low; other['low'] = 210-f.high
    a, _ = m.candidates(other, 'BTCUSDT', CFG)
    s = a[a.population.eq('signal')].iloc[0]
    assert s.side == -1 and s.stop == 117 and s.target_price == 110 and s.entry == 112
    assert m.candidates(f, 'XAUUSD', CFG, {'2026-10-19'})[0].empty


def winter_fixture():
    ix = pd.date_range('2026-01-26T05:00Z', periods=5*24*12, freq='5min')
    f = pd.DataFrame(np.tile([105., 110., 100., 105.], (len(ix), 1)), columns=m.old.OHLC, index=ix)
    f['count'] = 5
    for start, prices in [('2026-01-27T08:00Z', [96., 97., 93., 95.]),
                          ('2026-01-27T12:00Z', [95., 98., 92., 96.]),
                          ('2026-01-27T16:00Z', [96., 98., 94., 97.]),
                          ('2026-01-27T20:00Z', [97., 99., 94., 98.]),
                          ('2026-01-28T00:00Z', [98., 99., 97., 98.])]:
        t = pd.Timestamp(start)
        f.loc[(f.index >= t) & (f.index < t+pd.Timedelta(hours=4)), m.old.OHLC] = prices
    return f


def test_winter_monday_prefix_uses_source_5m_not_misaligned_4h_count():
    f = winter_fixture(); a, d = m.candidates(f, 'BTCUSDT', CFG)
    s = a[a.population.eq('signal')].iloc[0]
    assert pd.Timestamp(s.pin_time) == pd.Timestamp('2026-01-27T08:00Z')
    assert d['funnel']['1']['unknown_prefix'] == 0


def test_first_touch_in_boundary_bar_consumes_week_without_future_anchor_use():
    f = winter_fixture()
    # Monday NY ends Tuesday05UTC, within the04..08UTC4h bar.
    f.loc[pd.Timestamp('2026-01-27T06:00Z'), 'low'] = 93.
    a, d = m.candidates(f, 'BTCUSDT', CFG)
    assert a.empty and d['funnel']['1']['boundary_first_touches'] == 1
