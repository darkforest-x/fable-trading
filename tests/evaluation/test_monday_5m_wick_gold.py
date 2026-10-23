"""Ambiguous source minutes cannot become a chosen quote or trade outcome."""
import pandas as pd
from yoyo.evaluation import monday_5m_wick_gold as g


def test_conflicting_minutes_exclude_whole_bar_and_split():
    ix = pd.date_range('2026-10-19T10:00Z', periods=20, freq='min')
    f = pd.DataFrame([[100.,101.,99.,100.]]*20, columns=g.m.OHLC, index=ix.as_unit('us'))
    conflict = f.iloc[[7]].copy(); conflict['high'] = 102.
    identical = f.iloc[[1]].copy()
    result, quality = g.clean_minutes(pd.concat([f, conflict, identical]))
    assert len(result) == 3 and result['count'].eq(5).all()
    assert quality['conflicting_minutes'] == 1
    assert quality['excluded_five_minute_bars'] == 1
    assert result.quality_segment.tolist() == [0, 1, 1]
    assert g.invalid_monday_weeks(quality) == {'2026-10-19'}


def test_position_cannot_cross_a_conflicting_interval(tmp_path):
    ix = pd.date_range('2026-10-20T10:00Z', periods=15, freq='min')
    f = pd.DataFrame([[100.,101.,99.,100.]]*15, columns=g.m.OHLC, index=ix)
    f.iloc[10:] = [100.,120.,90.,110.]
    conflict = f.iloc[[7]].copy(); conflict['high'] = 102.
    bars, _ = g.clean_minutes(pd.concat([f, conflict]))
    first = bars[bars.quality_segment.eq(0)]
    entries = pd.DataFrame([dict(trade_id='L',pair_id='L',population='signal',symbol='XAUUSD',
        entry_i=0,side=1,entry=100.,stop=95.,target_price=105.,risk=5.)])
    rows, _ = g.m.execute(first, entries, 'XAUUSD', tmp_path, {'maker_fee':.0002,'taker_fee':.0005})
    assert not rows.closed.any()
    assert rows.net_r.isna().all()
