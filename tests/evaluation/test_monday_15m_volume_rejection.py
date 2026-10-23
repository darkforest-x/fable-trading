"""Case2 causality, first-touch and volume semantics; engine reused from case1."""
import numpy as np
import pandas as pd
from yoyo.evaluation import monday_15m_volume_rejection as m


CFG={'split':'2023-01-01T00:00:00Z','seed':20261023,'controls_per_signal':3}


def fixture():
    ix=pd.date_range('2026-10-19T04:00Z',periods=125,freq='15min')
    h=pd.DataFrame(np.tile([105.,110.,100.,105.,10.],(len(ix),1)),columns=[*m.old.OHLC,'volume'],index=ix)
    h.iloc[98]=[96.,97.,93.,95.,30.]
    h.iloc[99:102]=[[95.,97.,93.5,96.,10.],[96.,98.,94.,97.,10.],[97.,99.,94.,98.,10.]]
    h.iloc[102]=[98.,99.,97.,98.,10.]
    h['count']=3
    rows=np.repeat(h[[*m.old.OHLC,'volume']].to_numpy(),3,axis=0);rows[:,-1]/=3
    f=pd.DataFrame(rows,columns=[*m.old.OHLC,'volume'],index=pd.date_range(ix[0],periods=len(rows),freq='5min'))
    f['count']=5
    return h,f


def test_volume_peak_excludes_future_and_accepts_tie():
    h,_=fixture();h.loc[h.index[98],'volume']=10.
    pin,peak,confirm,_=m.pattern_mask(h,93.82,1)
    assert pin.iloc[98] and peak.iloc[98] and confirm.iloc[98]
    h.loc[h.index[97],'volume']=10.01
    assert not m.pattern_mask(h,93.82,1)[1].iloc[98]
    h.loc[h.index[97],'volume']=10.;h.loc[h.index[102],'volume']=10000.
    assert m.pattern_mask(h,93.82,1)[1].iloc[98]
    h.volume=0.
    assert not m.pattern_mask(h,93.82,1)[1].any()


def test_three_bodies_strict_inside_wicks_may_retouch():
    h,_=fixture();h.loc[h.index[100],'low']=92.
    assert m.pattern_mask(h,93.82,1)[2].iloc[98]
    h.loc[h.index[101],'open']=93.82
    assert not m.pattern_mask(h,93.82,1)[2].iloc[98]
    h,_=fixture();h.loc[h.index[100],'count']=2
    assert not m.pattern_mask(h,93.82,1)[2].iloc[98]


def test_entry_waits_for_third_close_and_prefix_invariant():
    _,f=fixture();a,detail=m.candidates(f,'BTCUSDT',CFG)
    s=a[a.population.eq('signal')].iloc[0]
    assert s.entry_i==102*3 and s.entry==98 and s.stop==93 and s.target_price==100
    assert pd.Timestamp(s.entry_time)==pd.Timestamp(s.pin_time)+pd.Timedelta(hours=1)
    changed=f.copy();changed.iloc[307:,:4]=[900.,1000.,800.,950.]
    b,_=m.candidates(changed,'BTCUSDT',CFG)
    pd.testing.assert_frame_equal(a[a.population.eq('signal')].reset_index(drop=True),b[b.population.eq('signal')].reset_index(drop=True))
    no_third=f.iloc[:102*3]
    assert m.candidates(no_third,'BTCUSDT',CFG)[0].empty
    assert detail['funnel']['1']['first_touches']==1


def test_nonqualifying_first_touch_consumes_the_week():
    _,f=fixture();f.iloc[97*3:98*3,2]=93.
    f.iloc[97*3:98*3,4]=1/3
    # Earlier touch has no peak volume but still rules out the later pin.
    assert m.candidates(f,'BTCUSDT',CFG)[0].empty


def test_gap_before_first_touch_is_not_a_known_first_touch():
    _,f=fixture();f=f.drop(f.index[96*3])
    a,d=m.candidates(f,'BTCUSDT',CFG)
    assert a.empty and d['funnel']['1']['unknown_prefix']==1


def test_short_mirrors_long_without_a_candle_color_filter():
    _,f=fixture();copy=f.copy()
    copy['open']=210-f.open;copy['close']=210-f.close
    copy['high']=210-f.low;copy['low']=210-f.high
    a,_=m.candidates(copy,'BTCUSDT',CFG)
    s=a[a.population.eq('signal')].iloc[0]
    assert s.side==-1 and s.stop==117 and s.target_price==110 and s.entry==112
