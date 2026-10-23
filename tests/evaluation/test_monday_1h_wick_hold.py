"""Strict wick exclusion, hourly confirmation causality and unchanged geometry."""
import numpy as np
import pandas as pd
from yoyo.evaluation import monday_1h_wick_hold as m

CFG = {'split': '2023-01-01T00:00:00Z', 'seed': 20261023,
       'controls_per_signal': 3, 'first_touch_only': True}


def fixture():
    ix = pd.date_range('2026-10-19T04:00Z', periods=45, freq='1h')
    h = pd.DataFrame(np.tile([105., 110., 100., 105.], (len(ix), 1)), columns=m.old.OHLC, index=ix)
    h.iloc[26] = [96., 97., 93., 95.]
    h.iloc[27:30] = [[95., 97., 94., 96.], [96., 98., 94., 97.], [97., 99., 94., 98.]]
    h.iloc[30] = [98., 99., 97., 98.]
    h['bars'] = 12; h['minutes'] = 60
    f = pd.DataFrame(np.repeat(h[m.old.OHLC].to_numpy(), 12, axis=0), columns=m.old.OHLC,
                     index=pd.date_range(ix[0], periods=len(h)*12, freq='5min'))
    f['count'] = 5
    return h, f


def test_pin_body_must_not_touch_and_confirmation_wicks_cannot_touch():
    h, _ = fixture()
    assert all(x.iloc[26] for x in m.pattern_mask(h, 93.82, 1))
    h.loc[h.index[26], 'open'] = 93.82
    assert not m.pattern_mask(h, 93.82, 1)[0].iloc[26]
    h, _ = fixture(); h.loc[h.index[28], 'low'] = 93.82
    assert not m.pattern_mask(h, 93.82, 1)[1].iloc[26]


def test_confirmations_are_three_consecutive_complete_hours():
    h, _ = fixture(); h.loc[h.index[28], 'minutes'] = 59
    assert not m.pattern_mask(h, 93.82, 1)[1].iloc[26]
    h, _ = fixture(); h = h.drop(h.index[28])
    assert not m.pattern_mask(h, 93.82, 1)[1].iloc[26]


def test_entry_after_four_hours_and_future_invariance():
    _, f = fixture(); a, _ = m.candidates(f, 'BTCUSDT', CFG)
    s = a[a.population.eq('signal')].iloc[0]
    assert s.entry_i == 30*12 and s.entry == 98 and s.stop == 93 and s.target_price == 100
    assert pd.Timestamp(s.entry_time) == pd.Timestamp(s.pin_time)+pd.Timedelta(hours=4)
    changed = f.copy(); changed.iloc[30*12+1:, :4] = [900., 1000., 800., 950.]
    b, _ = m.candidates(changed, 'BTCUSDT', CFG)
    pd.testing.assert_frame_equal(a[a.population.eq('signal')].reset_index(drop=True),
                                  b[b.population.eq('signal')].reset_index(drop=True))
    assert m.candidates(f.iloc[:30*12], 'BTCUSDT', CFG)[0].empty


def test_first_touch_consumes_week_even_when_body_crosses():
    _, f = fixture(); f.iloc[25*12:26*12, :4] = [92., 97., 91., 95.]
    assert m.candidates(f, 'BTCUSDT', CFG)[0].empty
    a, _ = m.candidates(f, 'BTCUSDT', {**CFG, 'first_touch_only': False})
    assert len(a[a.population.eq('signal')]) == 1


def test_missing_crypto_prefix_cannot_assert_first_touch():
    _, f = fixture(); f = f.drop(f.index[24*12])
    a, d = m.candidates(f, 'BTCUSDT', CFG)
    assert a.empty and d['funnel']['1']['unknown_prefix'] == 1


def test_short_mirror_and_gold_conflicted_monday_exclusion():
    _, f = fixture(); copy = f.copy()
    copy['open'] = 210-f.open; copy['close'] = 210-f.close
    copy['high'] = 210-f.low; copy['low'] = 210-f.high
    a, _ = m.candidates(copy, 'BTCUSDT', CFG)
    s = a[a.population.eq('signal')].iloc[0]
    assert s.side == -1 and s.stop == 117 and s.target_price == 110 and s.entry == 112
    assert m.candidates(f, 'XAUUSD', CFG, {'2026-10-19'})[0].empty


def test_spot_closure_not_filled_and_source_minutes_required():
    _, f = fixture(); f = f.drop(f.index[17*12:18*12])
    a, _ = m.candidates(f, 'XAUUSD', CFG)
    assert len(a[a.population.eq('signal')]) == 1
    # An incomplete pin is rejected even though12five-minute aggregates exist.
    f.loc[pd.Timestamp('2026-10-20T06:00Z'), 'count'] = 4
    assert m.candidates(f, 'XAUUSD', CFG)[0].empty
