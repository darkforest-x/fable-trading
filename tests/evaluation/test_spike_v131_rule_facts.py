"""Behavioral parity, repeated-gate removal and past-only feature boundaries."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_v128_recent as parent
from yoyo.evaluation.spike_v126_htf_recheck import _legacy
from yoyo.evaluation.spike_v131_rule_facts import context,legacy,structural,rules,state_features,bb_fields,candle_shape


@pytest.mark.parametrize('ohlc,side', [([.861,.876,.856,.872],1),([1.522,1.536,1.496,1.5],-1)])
def test_real_threshold_ties_preserve_original_division_order(ohlc,side):
    from yoyo.evaluation.spike_v7_fast import _shape
    o,h,l,c=map(lambda x:np.array([x]),ohlc)
    assert candle_shape(o,h,l,c,side)[0]==_shape(o,h,l,c,0,side)
    assert _shape(o,h,l,c,0,side)
    assert not (np.abs(c-o)>=.55*(h-l))[0]


def test_price_path_cache_discards_old_membership_and_nan_optional_fields(tmp_path):
    from yoyo.evaluation.spike_v131_rule_study import attempt_cache
    pd.DataFrame([dict(policy='rebreak',anchor_i=12,side=1,decision_i=17,status='expired',
        filled=False,net_r=np.nan,entry_i=np.nan,exit_i=np.nan,members=123,
        compression_run60=60,trade_key='old-id')]).to_csv(tmp_path/'15m.csv.gz',index=False)
    cached=attempt_cache(tmp_path,15)[12,1]
    assert cached['net_r'] is None and cached['decision_i']==17
    assert not {'members','compression_run60','trade_key','entry_i','exit_i'} & cached.keys()


def test_filled_censored_price_cache_keeps_numeric_unknown(tmp_path):
    from yoyo.evaluation.spike_v131_rule_study import attempt_cache
    pd.DataFrame([dict(policy='rebreak',anchor_i=12,side=1,decision_i=17,status='censored_boundary',
        filled=True,net_r=np.nan,entry_i=18,exit_i=20)]).to_csv(tmp_path/'15m.csv.gz',index=False)
    cached=attempt_cache(tmp_path,15)[12,1]
    assert np.isclose(cached['net_r'],np.nan,equal_nan=True)
    assert cached['entry_i']==18 and cached['exit_i']==20


def fixture():
    n=30
    d={k:np.full(n,v,float) for k,v in dict(open=95,high=99,low=90,close=95,atr=1,md=2,sb=1,
        ropeHigh=100,ropeLow=90,advance=2,volume=2,ph=100,pl=90,fast_hi=96,fast_lo=94).items()}
    d.update(ready=np.ones(n,bool),gap=np.zeros(n,bool),recent=np.ones(n,bool),middle=np.arange(n,dtype=float))
    d['open'][12:14]=[99,102];d['close'][12:14]=[102,104]
    d['high'][12:14]=[103,105];d['low'][12:14]=[98,101];d['md'][13]=3
    return d


@pytest.mark.parametrize('arm,field,value',[
    ('no_density','recent',False),('no_launch_volume','volume',0.),('no_advance','advance',0.),
    ('no_imacd','sb',10.),('no_candle_shape','open',101.9),('no_body_support','open',99.)])
def test_removing_repeated_rule_restores_an_otherwise_valid_launch(arm,field,value):
    d=fixture()
    if field=='open':d[field][12:14]=value
    else:d[field][:]=value
    assert not structural(d,1).any()
    assert structural(d,1,arm).any()


def test_mirrored_direction_and_pending_parent_cancellation():
    d=fixture();assert list(np.flatnonzero(structural(d,1)))==[13]
    m={k:v.copy() for k,v in d.items()}
    for a,b in [('high','low'),('ropeHigh','ropeLow'),('ph','pl'),('fast_hi','fast_lo')]:m[a],m[b]=200-d[b],200-d[a]
    for k in ['open','close','middle']:m[k]=200-d[k]
    for k in ['md','sb','advance']:m[k]=-d[k]
    np.testing.assert_array_equal(structural(d,1),structural(m,-1))
    d['gap'][13]=True
    assert not structural(d,1).any()


def bars(n=2600):
    rng=np.random.default_rng(81019)
    c=100*np.exp(np.cumsum(rng.normal(0,.008,n)))
    o=np.r_[c[0],c[:-1]];h=np.maximum(o,c)*1.002;l=np.minimum(o,c)*.998
    return pd.DataFrame(dict(open=o,high=h,low=l,close=c,volume=np.exp(rng.normal(5,1,n))),
                        index=pd.date_range('2023-01-01',periods=n,freq='1h',tz='UTC'))


def test_full_detector_matches_separate_reference_and_is_prefix_causal():
    f=parent._generic_facts(bars(),'BTC',.01,60);d=context(f['frame'],f['gap'])
    masks,raw,duration=rules(f['frame'],f['gap'],'BTC',60)
    np.testing.assert_array_equal(raw,f['side'])
    assert np.count_nonzero(raw)>0
    np.testing.assert_array_equal(masks['baseline',1]|masks['baseline',-1],f['v9'])
    for s in [1,-1]:
        expected=_legacy(f['frame'],f['gap'],pd.Series(d['advance'],index=f['frame'].index),pd.Series(d['volume'],index=f['frame'].index),s)
        for a,b in zip(expected,legacy(d,s)):np.testing.assert_allclose(a,b,equal_nan=True)
    short=f['frame'].iloc[:1500]
    sm,sr,sd=rules(short,f['gap'][:1500],'BTC',60)
    for key in masks:np.testing.assert_array_equal(sm[key],masks[key][:1500])
    np.testing.assert_allclose(sd,duration[:1500],equal_nan=True)


def test_relative_strength_missing_btc_or_gap_is_unknown_and_future_invariant():
    b=bars(100);b['atr']=1.;gap=np.zeros(100,bool);duration=np.ones(100)
    x=state_features(b,gap,b.close,duration,60)
    assert np.allclose(x.relative_btc20_atr.dropna(),0)
    missing=b.close.copy();missing.iloc[50]=np.nan
    y=state_features(b,gap,missing,duration,60)
    assert y.relative_btc20_atr.iloc[50:71].isna().all()
    gap[50]=True;z=state_features(b,gap,b.close,duration,60)
    assert z.advance20_atr.iloc[50:71].isna().all()
    prefix=state_features(b.iloc[:80],gap[:80],b.close.iloc[:80],duration[:80],60)
    np.testing.assert_allclose(z.iloc[:80],prefix,equal_nan=True)
