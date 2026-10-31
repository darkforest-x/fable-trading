"""Test real Pyharmonics candidates, their observable clock and frozen gates."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip('pyharmonics')
from pyharmonics import constants, utils
from yoyo.evaluation.monday_harmonic import scan_at, projection_band
from yoyo.evaluation.monday_order_only import admitted_population

ROOT=Path(__file__).resolve().parents[2]
CFG=json.loads((ROOT/'experiments/active/exp-monday-harmonic-confluence-20261031-v1/config.json').read_text())


def candles():
    ix=pd.date_range('2025-01-01',periods=260,freq='5min',tz='UTC')
    close=np.interp(np.arange(260),[0,20,65,110,155,229,259],[110,100,120,107.64,116.37852,106,80])
    return pd.DataFrame(dict(open=close,high=close+.005,low=close-.005,close=close,volume=100.),index=ix)


def mirror(f,center=220.):
    out=f.copy()
    out['open']=center-f.open;out['close']=center-f.close
    out['high']=center-f.low;out['low']=center-f.high
    return out


def test_real_library_finds_known_gartley_before_future_d():
    f=candles();m,p=scan_at(f,f.index[230],'TEST',1,104.28,CFG['harmonic'])
    assert m['admitted'] and m['observable']
    hit=[v for v in p if v['name']==constants.GARTLEY and v['confluence']]
    assert hit and not hit[0]['formed'] and not hit[0]['d_confirmed']
    assert pd.Timestamp(hit[0]['xabc_confirmed_at'])<=f.index[230]
    assert m['last_input_close']==str(f.index[230])


def test_touch_and_all_future_values_cannot_change_candidates():
    f=candles();cut=f.index[230]
    expected=scan_at(f,cut,'TEST',1,104.28,CFG['harmonic'])
    future=f.copy();future.loc[cut:,:]*=100
    assert scan_at(future,cut,'TEST',1,104.28,CFG['harmonic'])==expected
    assert scan_at(f.iloc[:230],cut,'TEST',1,104.28,CFG['harmonic'])==expected
    old=f.copy();old.loc[:cut-pd.Timedelta(minutes=5),'high']+=20
    assert scan_at(old,cut,'TEST',1,104.28,CFG['harmonic'])!=expected


def test_bearish_projection_and_search_are_price_mirrors():
    f=candles();cut=f.index[230]
    a,pa=scan_at(f,cut,'TEST',1,104.28,CFG['harmonic'])
    b,pb=scan_at(mirror(f),cut,'TEST',-1,220-104.28,CFG['harmonic'])
    assert a['admitted'] and b['admitted']
    x={p['name']:p for p in pa if p['confluence']};y={p['name']:p for p in pb if p['confluence']}
    assert x.keys()==y.keys()
    for k in x:
        assert np.isclose(x[k]['prz_low'],220-y[k]['prz_high'])
        assert np.isclose(x[k]['prz_high'],220-y[k]['prz_low'])


def test_endpoint_search_optimization_preserves_all_eligible_patterns():
    f=candles()
    for data,side,level in [(f,1,104.28),(mirror(f),-1,115.72)]:
        a,pa=scan_at(data,f.index[230],'TEST',side,level,CFG['harmonic'])
        b,pb=scan_at(data,f.index[230],'TEST',side,level,CFG['harmonic'],all_endpoints=True)
        assert pa==pb
        assert {k:v for k,v in a.items() if k!='rejected'}=={k:v for k,v in b.items() if k!='rejected'}


def test_ratio_tolerance_is_not_three_percent_of_market_price():
    defs=utils.get_pattern_definition(.03,constants.MATRIX_PATTERNS)
    lo,hi=projection_band([100,120,107.64,116],True,defs,constants.GARTLEY)
    assert lo<104.28<hi
    assert hi-lo<1
    assert not lo<=110<=hi


def test_gaps_and_short_history_are_unavailable_not_false_negatives():
    f=candles();cut=f.index[230]
    m,_=scan_at(f.drop(f.index[50]),cut,'TEST',1,104.28,CFG['harmonic'])
    assert not m['observable'] and m['reason']=='noncontiguous_history'
    m,_=scan_at(f.iloc[100:],cut,'TEST',1,104.28,CFG['harmonic'])
    assert not m['observable'] and m['reason']=='insufficient_history'


def test_touch_after_zone_failure_does_not_revive_the_old_candidate():
    f=candles();f.loc[f.index[225:230],['open','high','low','close']]-=8
    m,p=scan_at(f,f.index[230],'TEST',1,104.28,CFG['harmonic'])
    assert not m['admitted']


def test_external_filter_preserves_original_score_and_ema(monkeypatch,tmp_path):
    from yoyo.evaluation import monday_order_only as mo
    pop=pd.DataFrame(dict(score=[.1,.7],l2_order_score=[1,4]),index=['a','b'])
    monkeypatch.setattr(mo,'population',lambda cfg:pop.copy())
    p=tmp_path/'gates.csv';pd.DataFrame(dict(event=['a','b'],admitted=[True,False])).to_csv(p,index=False)
    got=admitted_population(dict(CFG,admission_events=str(p)))
    pd.testing.assert_frame_equal(got,pop.iloc[:1])
    pd.testing.assert_frame_equal(admitted_population({'min_order':3}),pop.iloc[1:])


def test_dependency_rates_and_original_clock_are_frozen():
    old=json.loads((ROOT/CFG['baseline_comparison']/'config.json').read_text())
    for k in ('maker_fee','taker_fee','breakeven_bp','lock_bp','v2_threshold','v3_cut','far_score','add_pct','v3_sizing','score_column','baseline_arm','non_target_slippage_bp'):
        assert CFG[k]==old[k]


def test_native_overlay_generates_only_originally_eligible_rules():
    from yoyo.evaluation.monday_order_only import construct
    rows=[]
    for event in ('low','mid','high','spike'):
        for minutes in (5,60):
            for mode in ('near_monday_edge','range_0.618','opposite_1618'):
                rows.append(dict(event_id=event,reclaim_minutes=minutes,exit_mode=mode,
                    trade_id=f'{event}:{minutes}:{mode}',control_id=0,side=1,entry=100.,
                    target_price=130.,entry_i=0,expire_i=10,end_i=20))
    pop=pd.DataFrame(dict(score=[.3,.55,.7,.7],l2_order_score=[0,0,0,3]),index=['low','mid','high','spike'])
    x=construct(pd.DataFrame(rows),pop,np.array([110.]),.01,CFG)
    assert set(x[x.rule=='spike'].event)=={'spike'}
    assert set(x[x.rule=='p30'].event)=={'mid','high','spike'}
    assert set(x[(x.rule=='p30')&(x.event=='mid')].exit_mode)=={'range_0.618'}
    assert set(x[x.rule=='add'].event)=={'high','spike'}
    assert set(x[x.rule=='v2'].event)==set(pop.index)


def test_library_candidate_name_does_not_override_actual_xabc_ratios():
    from yoyo.evaluation.monday_harmonic_summary import valid_geometry
    defs=utils.get_pattern_definition(.03,constants.MATRIX_PATTERNS)
    good=dict(name=constants.GARTLEY,prices=[100,120,107.64,116.37852,106])
    assert valid_geometry(good,defs)
    # A joined different A can keep a name while invalidating the ABC ratio.
    assert not valid_geometry(dict(good,prices=[100,120,107.64,125,106]),defs)
    assert not valid_geometry(dict(good,prices=[100,120,110,116.37852,106]),defs)
