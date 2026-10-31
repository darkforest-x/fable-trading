"""Causality and native execution fixtures for the finite reclaim study."""
import numpy as np
import pandas as pd
import unittest
from yoyo.evaluation import reclaim_strategy_engine as engine
from yoyo.evaluation import top20_12h_strategies as s
from yoyo.evaluation.top20_12h_strategy_analysis import combine,periods

CFG=dict(maker_fee=.0002,taker_fee=.0005,pending_hours=24,seed=1,controls_per_signal=3)


def frame(prices,start='2024-01-02T12:00:00Z'):
    return pd.DataFrame(prices,columns=s.OHLC,index=pd.date_range(start,periods=len(prices),freq='15min'))


def test_limit_cannot_use_high_before_its_fill():
    prices=np.array([[110.,125.,95.,105.]])
    seg=engine.path_segments(prices,'OHLC')
    hit=engine.crossing(seg,0,len(seg),100.,1)
    assert hit==(2,100.)
    tail=engine.after_entry(seg,hit[0],len(seg),hit[1])
    assert engine.native_barriers(tail,90.,120.,1) is None
    other=engine.path_segments(prices,'OLHC')
    i,p=engine.crossing(other,0,len(other),100.,1)
    assert engine.native_barriers(engine.after_entry(other,i,len(other),p),90.,120.,1)[2]=='target'


def test_gap_target_precedes_later_stop_and_stop_uses_gap_quote():
    f=frame([[100,101,99,100],[120,130,80,100]])
    seg=engine.path_segments(f.values,'OHLC')
    assert engine.native_barriers(seg,90,110,1)==(4,110,'target')
    f.iloc[1]=[80,120,70,100];seg=engine.path_segments(f.values,'OHLC')
    assert engine.native_barriers(seg,90,110,1)==(4,80,'stop')


def test_two_intrabar_paths_and_short_mirror():
    f=frame([[100,120,80,100]])
    assert engine.native_barriers(engine.path_segments(f.values,'OHLC'),90,110,1)[2]=='target'
    assert engine.native_barriers(engine.path_segments(f.values,'OLHC'),90,110,1)[2]=='stop'
    assert engine.native_barriers(engine.path_segments(f.values,'OHLC'),110,90,-1)[2]=='stop'
    assert engine.native_barriers(engine.path_segments(f.values,'OLHC'),110,90,-1)[2]=='target'


def test_micro_prices_exact_touch_and_half_leg_fees():
    for scale in [1.,1e-7]:
        f=frame(np.array([[100.,110.,99.,105.]])*scale)
        assert engine.native_barriers(engine.path_segments(f.values,'OHLC'),90*scale,110*scale,1)[2]=='target'
        base=dict(side=1,weight=.5,entry=100*scale,exit_price=110*scale,risk=10*scale,
                  entry_fee=.0002,reason='target',closed=True,entry_time='2024-01-01',exit_time='2024-01-02',leg_id='x')
        result,fills=engine.account_legs(pd.DataFrame([base]),CFG)
        np.testing.assert_allclose(result.net_r.iloc[0],.5-(100*.0002+110*.0002)/10*.5)
        assert len(fills)==2


def test_censored_position_remains_open_with_no_fake_exit_fee():
    base=dict(side=1,weight=1.,entry=100.,exit_price=105.,risk=10.,
              entry_fee=.0005,reason='censored',closed=False,entry_time='2024-01-01',exit_time='2024-01-02',leg_id='x')
    result,fills=engine.account_legs(pd.DataFrame([base]),CFG)
    assert len(fills)==1
    assert np.isclose(result.net_r.iloc[0],.495)


def signal_fixture():
    f=frame([[104.,108.,102.,105.]]*150)
    f.iloc[10]=[104.,106.,90.,103.]
    f.iloc[20]=[104.,110.,103.,105.]
    row=dict(event_id='X',symbol='X',week='2024-01-01',side=1,
        signal_open=str(f.index[0]),signal_close=str(f.index[48]),
        expiry=str(f.index[-1]),low=90.,high=110.,level=100.,monday_low=110.,monday_high=120.)
    return f,row


def test_no_retroactive_fill_inside_12h_signal():
    f,r=signal_fixture();plan,status=s.signal_plan(f,r,'A_limit',.1,s.gap_ends(f),CFG)
    assert plan['start']==48
    assert s.fill_plan(f,engine.path_segments(f.values,'OHLC'),plan,CFG) is None


def test_pullback_frozen_impulse_and_next_open_causality():
    f,r=signal_fixture();f.iloc[49]=[104,105,97,103];f.iloc[50]=[103,108,102,107]
    plan,status=s.signal_plan(f,r,'B_618',.1,s.gap_ends(f),CFG)
    assert np.isclose(plan['retrace'],97.64)
    assert plan['start']==51 and np.isclose(plan['stop'],89.9)
    altered=f.copy();altered.iloc[52:]=[1000,1200,500,900]
    after,_=s.signal_plan(altered,r,'B_618',.1,s.gap_ends(altered),CFG)
    assert plan==after


def test_sweep_uses_new_closed_needle_and_cancels_bad_open():
    f,r=signal_fixture();f.iloc[48]=[95,97,88,92];f.iloc[49]=[92,96,91,94]
    p,_=s.signal_plan(f,r,'C_sweep',.1,s.gap_ends(f),CFG)
    assert p['start']==49 and np.isclose(p['stop'],87.9)
    f.iloc[49]=[87,96,85,94]
    p,reason=s.signal_plan(f,r,'C_sweep',.1,s.gap_ends(f),CFG)
    assert p is None and reason=='opening_quote_invalidates_stop'


def test_missing_bar_censors_even_if_next_available_is_expiry():
    f=frame([[100,101,99,100],[100,101,99,100]])
    f.index=pd.DatetimeIndex([f.index[0],f.index[0]+pd.Timedelta(days=7)])
    entry=dict(event_id='X',mode='A_limit',side=1,entry_i=0,entry_segment=0,entry=100.,
        risk=10.,stop=90.,tick=.1,monday_low=150.,monday_high=160.,entry_fee=.0002,
        entry_time=str(f.index[0]),control=False)
    x=s.execute_targets(f,engine.path_segments(f.values,'OHLC'),entry,s.gap_ends(f),'OHLC',CFG)
    assert all(r['reason']=='censored' for r in x)


def test_partial_has_two_framework_half_positions_and_unchanged_stop():
    f=frame([[100,160,99,155],[155,200,90,100]])
    entry=dict(event_id='X',mode='A_limit',side=1,entry_i=0,entry_segment=0,entry=100.,
        risk=10.,stop=90.,tick=.1,monday_low=150.,monday_high=170.,entry_fee=.0002,
        entry_time=str(f.index[0]),control=False)
    x=s.execute_targets(f,engine.path_segments(f.values,'OHLC'),entry,s.gap_ends(f),'OHLC',CFG)
    partial=[r for r in x if r['target_name'].startswith('partial_')]
    assert len(partial)==2 and sum(r['weight'] for r in partial)==1.
    assert all(r['stop']==90. for r in partial)
    out,fills=engine.account_legs(pd.DataFrame(partial),CFG)
    assert len(fills)==4 and out.closed.all()


def test_time_split_purges_crossing_positions():
    x=pd.DataFrame(dict(entry_time=['2024-12-30T00:00Z','2025-02-01T00:00Z'],
                        exit_time=['2025-01-02T00:00Z','2025-02-02T00:00Z'],closed=[True,True]))
    p=periods(x)
    assert not p['selection_pre2025'].any()
    assert p['evaluation_2025'].tolist()==[False,True]


def test_fixed_candidate_space():
    c=s.candidates()
    assert len(c)==32 and len({r['config'] for r in c})==32


def test_batch_and_scalar_accounting_agree():
    rows=[]
    for i,(side,exit_,weight) in enumerate([(1,120.,1.),(-1,110.,.5),(1,90.,.5)]):
        rows.append(dict(side=side,weight=weight,entry=100.,exit_price=exit_,risk=10.,
            entry_fee=.0005,reason='stop',closed=True,entry_time='2024-01-01',exit_time='2024-01-02',leg_id=str(i)))
    batch,_=engine.account_legs(pd.DataFrame(rows),CFG)
    scalar=[engine.account_legs(pd.DataFrame([r]),CFG)[0].net_r.iloc[0] for r in rows]
    np.testing.assert_allclose(batch.net_r,scalar)


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(unittest.FunctionTestCase(f) for name,f in globals().items()
                              if name.startswith('test_') and callable(f))
