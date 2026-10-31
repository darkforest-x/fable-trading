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


def higher_candles(interval):
    from yoyo.evaluation.monday_harmonic import interval_step
    hc=dict(CFG['harmonic'],interval=interval);step=interval_step(hc);factor=int(step/pd.Timedelta(minutes=5))
    f=candles();f.index=pd.date_range('2025-01-01',periods=len(f),freq=step,tz='UTC')
    raw=f.loc[f.index.repeat(factor)].copy();raw.index=pd.date_range(f.index[0],periods=len(raw),freq='5min')
    raw['volume']/=factor
    return raw,hc,f.index[230]+pd.Timedelta(minutes=5)


@pytest.mark.parametrize('interval',['15m','1h'])
def test_higher_timeframe_uses_only_completed_bars_and_preserves_causality(interval):
    from yoyo.evaluation.monday_harmonic import scan_raw,interval_step
    raw,hc,cut=higher_candles(interval);expected=scan_raw(raw,cut,'TEST',1,104.28,hc)
    m,patterns=expected
    assert m['admitted'] and m['last_input_close']==str(cut.floor(interval_step(hc)))
    assert m['invalidation_last_close']==str(cut)
    assert all(pd.Timestamp(t)+interval_step(hc)<=cut for p in patterns for t in p['points'])
    assert all(pd.Timestamp(p['xabc_confirmed_at'])<=cut for p in patterns)
    changed=raw.copy();changed.loc[cut:,:]*=100
    assert scan_raw(changed,cut,'TEST',1,104.28,hc)==expected
    assert scan_raw(raw.loc[raw.index<cut],cut,'TEST',1,104.28,hc)==expected


@pytest.mark.parametrize('interval',['15m','1h'])
def test_known_five_minute_tail_can_expire_but_not_form_a_higher_pattern(interval):
    from yoyo.evaluation.monday_harmonic import scan_raw,interval_step
    raw,hc,cut=higher_candles(interval);boundary=cut.floor(interval_step(hc))
    assert scan_raw(raw,cut,'TEST',1,104.28,hc)[0]['admitted']
    raw.loc[boundary,'low']=80.
    assert not scan_raw(raw,cut,'TEST',1,104.28,hc)[0]['admitted']


@pytest.mark.parametrize('interval',['15m','1h'])
def test_missing_constituent_cannot_be_aggregated_as_a_complete_bar(interval):
    from yoyo.evaluation.monday_harmonic import scan_raw,aggregate_bars,interval_step
    raw,hc,cut=higher_candles(interval);step=interval_step(hc)
    removed=raw.index[50];broken=raw.drop(removed)
    assert aggregate_bars(broken,hc).loc[removed.floor(step)].isna().all()
    m,_=scan_raw(broken,cut,'TEST',1,104.28,hc)
    assert not m['observable'] and m['reason']=='nonfinite_history'
    m,_=scan_raw(raw.drop(cut-pd.Timedelta(minutes=5)),cut,'TEST',1,104.28,hc)
    assert not m['observable'] and m['reason']=='noncontiguous_partial_tail'


def test_multiple_native_caches_keep_order_and_reject_fee_changes(tmp_path):
    from yoyo.evaluation.monday_order_only import prior_studies,audit_prior_studies
    a=tmp_path/'a';b=tmp_path/'b';a.mkdir();b.mkdir()
    for p in (a,b):(p/'config.json').write_text(json.dumps(CFG))
    cfg=dict(CFG,reuse_study=str(a),reuse_studies=[str(b),str(a)])
    assert prior_studies(cfg)==[a,b]
    assert audit_prior_studies(cfg,{'prior_studies_used':[str(a)]})==[a]
    assert audit_prior_studies(cfg,{'prior_studies_used':[]})==[]
    changed=dict(CFG,maker_fee=.001);(b/'config.json').write_text(json.dumps(changed))
    with pytest.raises(ValueError,match='maker_fee'):prior_studies(cfg)


def test_higher_configs_change_timeframe_without_changing_original_trading_rules():
    for tf in ('15m','1h'):
        cfg=json.loads((ROOT/f'experiments/active/exp-monday-harmonic-{tf}-confluence-20261031-v1/config.json').read_text())
        for k in ('maker_fee','taker_fee','non_target_slippage_bp','breakeven_bp','lock_bp','v2_threshold','v3_cut','far_score','add_pct','v3_sizing','score_column','baseline_arm','baseline_ledger','seed'):
            assert cfg[k]==CFG[k]
        for k in ('lookback_bars','min_bars','peak_spacing','fib_tolerance','percent_c_to_d'):
            assert cfg['harmonic'][k]==CFG['harmonic'][k]
        assert cfg['harmonic']['interval']==tf


def test_holm_keeps_untestable_empty_arms_in_the_declared_family():
    from yoyo.evaluation.monday_harmonic_compare import holm
    np.testing.assert_allclose(holm([.03,None,.001,.01]),[.06,1.,.004,.03])
