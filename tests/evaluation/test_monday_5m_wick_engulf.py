"""Behavioral tests: source timezone, causality and next-open native execution."""
import numpy as np
import pandas as pd
from yoyo.evaluation import monday_5m_wick_engulf as m


def frame(rows):
    f=pd.DataFrame(rows,columns=m.OHLC,index=pd.date_range('2026-10-20',periods=len(rows),freq='5min',tz='UTC'))
    f['count']=5
    return f


def test_histdata_est_is_fixed_in_winter_and_summer():
    idx=m.histdata_index(['20260105 000000','20260706 000000'])
    assert list(idx.hour)==[5,5]
    assert list(idx.tz_convert(m.TZ).hour)==[0,1]


def test_adjacent_engulf_body_and_wick_are_directional():
    f=frame([[103,104,99,101],[101,105,100,104],[104,105,103,103.5]])
    assert m.signal_mask(f,100,1).tolist()==[False,True,False]
    f.loc[f.index[0],'close']=99.5
    assert not m.signal_mask(f,100,1).any()
    f=frame([[97,101,96,99],[99,100,95,96],[96,97,95,96]])
    assert m.signal_mask(f,100,-1).tolist()==[False,True,False]
    f.index=pd.DatetimeIndex([f.index[0],f.index[1]+m.STEP,f.index[2]+m.STEP])
    assert not m.signal_mask(f,100,-1).any()


def test_completed_monday_and_future_prefix_invariance():
    ix=pd.date_range('2026-10-19T04:00:00Z',periods=310,freq='5min')
    f=pd.DataFrame(np.tile([105.,110.,100.,105.],(len(ix),1)),columns=m.OHLC,index=ix)
    f['count']=5
    # Monday lower1618 is93.82; red body stays inside, then green engulfs it.
    f.iloc[290,:4]=[95.,96.,93.,94.]
    f.iloc[291,:4]=[94.,97.,94.,96.]
    f.iloc[292,:4]=[96.,98.,95.,97.]
    cfg={'split':'2023-01-01T00:00:00Z','seed':1,'controls_per_signal':3}
    a,_=m.candidates(f,'BTCUSDT',cfg)
    x=a[(a.population=='signal') & (a.entry_i==292)].iloc[0]
    assert x.entry==96 and x.stop==93 and x.target_price==100
    changed=f.copy();changed.iloc[293:,:4]=[900.,1000.,800.,950.]
    b,_=m.candidates(changed,'BTCUSDT',cfg)
    pd.testing.assert_frame_equal(a[(a.population=='signal') & (a.entry_i<=292)].reset_index(drop=True),
                                  b[(b.population=='signal') & (b.entry_i<=292)].reset_index(drop=True))
    incomplete=f.drop(f.index[2])
    c,_=m.candidates(incomplete,'BTCUSDT',cfg)
    assert c.empty


def test_native_next_open_and_same_bar_collision(tmp_path):
    f=frame([[100,102,98,101],[103,110,95,104],[104,105,103,104]])
    entries=pd.DataFrame([dict(trade_id='L',pair_id='L',population='signal',symbol='BTCUSDT',entry_i=1,side=1,entry=103.,stop=99.,target_price=108.,risk=4.),
                          dict(trade_id='S',pair_id='S',population='signal',symbol='BTCUSDT',entry_i=1,side=-1,entry=103.,stop=108.,target_price=99.,risk=5.)])
    rows,audit=m.execute(f,entries,'BTCUSDT',tmp_path,{'maker_fee':.0002,'taker_fee':.0005})
    result=rows.set_index('trade_id')
    assert result.loc['L','entry']==103  # Not preceding close101 nor confirmation high.
    assert result.loc['L','reason']==4 and result.loc['S','reason']==1
    assert result.ambiguous_bar.all()
    assert result.loc['L','stop_first_gross_r']==-1
    assert np.isclose(result.loc['L','fee_r'],(103*.0005+108*.0002)/4)
    assert audit['oracle_checked']==2


def test_native_gap_censor_and_end_not_counted_as_closed(tmp_path):
    f=frame([[100,101,99,100],[100,101,99,100],[100,101,99,100]])
    f.index=pd.DatetimeIndex([f.index[0],f.index[1],f.index[2]+pd.Timedelta(days=1)])
    entries=pd.DataFrame([dict(trade_id='L',pair_id='L',population='signal',symbol='BTCUSDT',entry_i=1,side=1,entry=100.,stop=90.,target_price=110.,risk=10.)])
    rows,_=m.execute(f,entries,'BTCUSDT',tmp_path,{'maker_fee':.0002,'taker_fee':.0005})
    assert not rows.closed.any()
    assert rows.exit_original_i.iloc[0]==1
