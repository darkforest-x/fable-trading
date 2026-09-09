"""Synthetic confirmation-time, stop-first and previous protection tests."""
import pandas as pd
from yoyo.evaluation.bico_exit_case import simulate


def frames():
    b=pd.DataFrame(dict(open=[100.,100.,101.,100.,97.],high=[101.,102.,102.,101.,98.],
        low=[99.,100.,99.,97.,96.],close=[100.,101.,100.,98.,97.]),
        index=pd.date_range('2024-01-01',periods=5,freq='h',tz='UTC'))
    f=pd.DataFrame(dict(md=[1.,1.,1.,1.,1.],sb=[0.,0.,0.,0.,0.],close=b.close,
        sma20=[99.,102.,100.5,100.,99.]),index=b.index)
    return b,f


def test_previous_protection_not_current_ma_and_next_open_fill():
    b,f=frames();r=simulate(b,f,0,90.,'sma20',80.)
    assert r['trigger_i']==3 and r['exit_i']==4 and r['exit_price']==97.
    assert r['held_hours_lower']==r['held_hours_upper']==3


def test_initial_stop_wins_over_same_bar_target():
    b,f=frames();b.iloc[1]=[100.,150.,89.,101.]
    r=simulate(b,f,0,90.,'fixed3r',80.)
    assert r['exit_i']==1 and r['exit_price']==90 and r['known_peak_r']==0
    assert r['held_hours_lower']==0 and r['held_hours_upper']==1


def test_known_open_beyond_target_precedes_later_bar_stop_touch():
    b,f=frames();b.iloc[2]=[140.,150.,89.,101.]
    r=simulate(b,f,0,90.,'fixed3r',80.)
    assert r['exit_i']==2 and r['exit_price']==130 and r['exit_timing']=='open'


def test_md_close_exits_next_open_even_after_large_intrabar_high():
    b,f=frames();f.loc[f.index[1],'md']=0.;b.loc[b.index[1],'high']=190.
    r=simulate(b,f,0,90.,'episode',80.)
    assert r['exit_i']==2 and r['exit_price']==101. and r['known_peak_r']==9.
    assert r['net_r']<r['known_peak_r']
