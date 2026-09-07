"""Synthetic fixed40 reference parity, volume quality and causal clocks."""
from copy import deepcopy

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal, assert_series_equal

from yoyo.data.hourly_impulse import add_features, make_entries
from yoyo.evaluation.hourly_impulse_vwma import (
    FEATURE_COLUMNS, REFERENCE_COLUMNS, add_reference_features,
)


def bars(n=150, seed=3, constant_volume=None):
    rng=np.random.default_rng(seed)
    price=100+np.cumsum(rng.normal(0,.35,n))
    o=price+rng.normal(0,.15,n);c=price+rng.normal(0,.2,n)
    data=pd.DataFrame({'open_time':pd.date_range('2024-01-01T00:00:00Z',periods=n,freq='h'),
                       'open':o,'high':np.maximum(o,c)+.5,'low':np.minimum(o,c)-.5,'close':c,
                       'volume':rng.uniform(1,100,n) if constant_volume is None else constant_volume,
                       'segment_id':0})
    data.attrs['bar_minutes']=60
    return data


@pytest.mark.parametrize('seed',range(5))
@pytest.mark.parametrize('gap',[False,True])
def test_sma_all_old_fields_exact_frozen_parity(seed,gap):
    data=bars(seed=seed)
    if gap:data.loc[72:,'open_time']+=pd.Timedelta(hours=3);data.loc[72:,'segment_id']=1
    data.index=np.arange(len(data))*3
    data['irrelevant_source']='kept';data.attrs['source']={'fixed':True}
    expected=add_features(data,'SMA',40)
    actual=add_reference_features(data,'SMA')
    assert_frame_equal(actual[expected.columns],expected,check_exact=True)
    assert_frame_equal(make_entries(actual,{}),make_entries(expected,{}),check_exact=True)
    assert actual.attrs['ma_kind']=='SMA' and actual.attrs['ma_length']==40


@pytest.mark.parametrize('volume',[1.,10.,.1,1000000.])
def test_uniform_volume_matches_sma_and_all_dependents(volume):
    data=bars(constant_volume=volume)
    sma=add_reference_features(data,'SMA');vw=add_reference_features(data,'VWMA')
    assert_frame_equal(sma[list(FEATURE_COLUMNS)],vw[list(FEATURE_COLUMNS)],check_exact=True)
    assert_frame_equal(make_entries(sma,{}),make_entries(vw,{}),check_exact=True)


@pytest.mark.parametrize('price,volume',[(.1,.1),(.3,1.1),(30.1,1.1),(100.3,.1),(102.2,1.1)])
def test_equal_weights_preserve_exact_bullish_ties_without_division_roundoff(price,volume):
    data=bars(80,constant_volume=volume)
    data[['open','high','low','close']]=price
    sma=add_reference_features(data,'SMA');vw=add_reference_features(data,'VWMA')
    assert_frame_equal(sma[list(FEATURE_COLUMNS)],vw[list(FEATURE_COLUMNS)],check_exact=True)
    assert vw.ma_side.iloc[39:].eq(1).all()


def test_equal_volume_identity_is_local_to_complete_current_window():
    data=bars(120,constant_volume=.1)
    data.loc[45,'volume']=.2
    sma=add_reference_features(data,'SMA');vw=add_reference_features(data,'VWMA')
    assert not sma.ma.iloc[45:85].equals(vw.ma.iloc[45:85])
    assert_series_equal(sma.ma.iloc[85:],vw.ma.iloc[85:],check_exact=True)
    assert_series_equal(sma.ma_slope_atr.iloc[88:],vw.ma_slope_atr.iloc[88:],check_exact=True)


def test_one_large_weight_changes_side_slope_and_prior_cross_counter():
    data=bars(100,constant_volume=1.)
    price=np.array([100.]*100);price[10]=120.;price[39:]=102.
    data['open']=price;data['close']=price;data['high']=price+1;data['low']=price-1
    data.loc[10,'volume']=1000.
    sma=add_reference_features(data,'SMA');vw=add_reference_features(data,'VWMA')
    assert sma.ma_side.iloc[39]==1 and vw.ma_side.iloc[39]==-1
    assert vw.ma_side.iloc[49]==-1 and vw.ma_side.iloc[50]==1
    assert sma.ma_slope_atr.iloc[50]<0 and vw.ma_slope_atr.iloc[50]<sma.ma_slope_atr.iloc[50]
    assert vw.cross_count24.iloc[50]==0 and vw.cross_count24.iloc[51]==1
    assert sma.cross_count24.iloc[51]==0
    for i in range(39,len(data)):
        window=data.iloc[i-39:i+1]
        expected=((window.high+window.low)/2*window.volume).sum()/window.volume.sum()
        assert vw.ma.iloc[i]==pytest.approx(expected,rel=1e-12)
    assert vw.ma_slope_atr.iloc[50]==pytest.approx((vw.ma.iloc[50]-vw.ma.iloc[47])/(3*vw.atr.iloc[50]))


def test_all_non_reference_features_identical_between_arms():
    data=bars();sma=add_reference_features(data,'SMA');vw=add_reference_features(data,'VWMA')
    common=[c for c in FEATURE_COLUMNS if c not in ('ma','ma_side','ma_slope_atr','cross_count24')]
    assert_frame_equal(sma[common],vw[common],check_exact=True)
    assert not sma.ma.equals(vw.ma)


@pytest.mark.parametrize('bad',[np.nan,None,np.inf,-np.inf,-1.,True,False,'missing'])
def test_invalid_volume_only_vwma_window_unknown_and_recovers_without_filling(bad):
    data=bars(125);clean=add_reference_features(data,'SMA')
    data['volume']=data.volume.astype(object);data.loc[50,'volume']=bad
    sma=add_reference_features(data,'SMA');vw=add_reference_features(data,'VWMA')
    unaffected=[c for c in FEATURE_COLUMNS if c!='volume_ratio']
    assert_frame_equal(sma[unaffected],clean[unaffected],check_exact=True)
    assert sma.reference_known.iloc[39:].all()
    assert vw.reference_known.iloc[39:50].all() and not vw.reference_known.iloc[50:90].any()
    assert vw.reference_known.iloc[90:].all()
    assert vw.reference_reason.iloc[50:90].eq('invalid_volume').all()
    assert vw.ma.iloc[50:90].isna().all() and vw.ma_side.iloc[50:90].eq(0).all()
    assert vw.reference_volume_sum.iloc[50:90].isna().all()
    assert vw.reference_volume_invalid_count.iloc[50:90].eq(1).all()
    assert not vw.reference_volume_valid.iloc[50] and pd.isna(vw.volume.iloc[50])
    assert sma.volume_ratio.iloc[50:71].isna().all()
    assert_series_equal(sma.volume_ratio,vw.volume_ratio,check_exact=True)


def test_absent_volume_does_not_shrink_sma_but_vwma_never_known():
    data=bars();expected=add_reference_features(data,'SMA');missing=data.drop(columns='volume')
    sma=add_reference_features(missing,'SMA');vw=add_reference_features(missing,'VWMA')
    assert_series_equal(sma.ma,expected.ma,check_exact=True)
    assert sma.reference_known.iloc[39:].all() and not vw.reference_known.any()
    assert vw.reference_reason.iloc[39:].eq('invalid_volume').all()
    assert sma.volume.isna().all() and sma.volume_ratio.isna().all()
    assert sma.attrs['volume_column_present'] is False


def test_zero_volume_is_valid_but_zero_sum_is_unknown_not_sma_failure():
    data=bars(100,constant_volume=0.)
    sma=add_reference_features(data,'SMA');vw=add_reference_features(data,'VWMA')
    assert sma.reference_known.iloc[39:].all() and not vw.reference_known.any()
    assert vw.reference_volume_valid.all() and vw.reference_volume_invalid_count.eq(0).all()
    assert vw.reference_reason.iloc[:39].eq('warmup').all()
    assert vw.reference_reason.iloc[39:].eq('zero_volume_sum').all()
    assert vw.reference_volume_sum.iloc[39:].eq(0).all()
    data.loc[40,'volume']=1.
    mixed=add_reference_features(data,'VWMA')
    assert mixed.reference_known.iloc[40:80].all() and not mixed.reference_known.iloc[80]
    assert mixed.ma.iloc[40]==mixed.hl2.iloc[40]


@pytest.mark.parametrize('reference',['SMA','VWMA'])
def test_exact_40_window_and_real_gap_restart_all_features(reference):
    data=bars(120);data.loc[55:,'open_time']+=pd.Timedelta(hours=1);data.loc[55:,'segment_id']=1
    out=add_reference_features(data,reference)
    assert out.reference_count.iloc[:40].tolist()==list(range(1,41))
    assert not out.reference_known.iloc[:39].any() and out.reference_known.iloc[39]
    assert out.reference_count.iloc[55]==1 and not out.reference_known.iloc[55:94].any() and out.reference_known.iloc[94]
    assert out.atr.iloc[55:68].isna().all() and pd.notna(out.atr.iloc[68])
    assert out.ma_slope_atr.iloc[94:97].isna().all()
    assert out.reference_window_start.iloc[94]==data.open_time.iloc[55]
    assert out.reference_available_at.eq(out.open_time+pd.Timedelta(hours=1)).all()
    assert_frame_equal(out,add_reference_features(data.drop(columns='segment_id'),reference))


@pytest.mark.parametrize('reference',['SMA','VWMA'])
def test_prefix_truncation_and_future_ohlcv_mutation_do_not_change_past(reference):
    data=bars();before=add_reference_features(data,reference)
    shorter=add_reference_features(data.iloc[:85],reference)
    assert_frame_equal(before.iloc[:85],shorter,check_exact=True)
    data.loc[85:,['open','high','low','close']]*=3
    data.loc[85:,'volume']*=1000
    changed=add_reference_features(data,reference)
    assert_frame_equal(before.iloc[:85],changed.iloc[:85],check_exact=True)


def test_inputs_not_mutated_unrelated_fields_ignored():
    data=bars();data.index=[7]*len(data);data.attrs['owner']={'unchanged':True}
    data['old_pnl']=[object()]*len(data);data['old_exit_time']='not-a-clock'
    before=data.copy(deep=True);attrs=deepcopy(data.attrs)
    out=add_reference_features(data,'VWMA')
    assert_frame_equal(data,before);assert data.attrs==attrs
    assert out.old_exit_time.eq('not-a-clock').all() and out.index.equals(pd.RangeIndex(len(data)))


@pytest.mark.parametrize('bad',['EMA','sma','VWAP','',None,True,40])
def test_no_reference_or_length_search(bad):
    with pytest.raises(ValueError):add_reference_features(bars(),bad)
    with pytest.raises(TypeError):add_reference_features(bars(),ma_length=20)


@pytest.mark.parametrize('field,bad',[('open',0.),('close',np.nan),('high',np.inf),('low',-1.),
                                   ('open',True),('high',99.),('low',105.),('close','bad')])
def test_bad_ohlc_fails_for_both_arms(field,bad):
    data=bars();data[field]=data[field].astype(object);data.loc[10,field]=bad
    for reference in ('SMA','VWMA'):
        with pytest.raises(ValueError):add_reference_features(data,reference)


@pytest.mark.parametrize('bad',[None,1704067200,'2024-01-01 00:00:00',
    '2024-01-01T01:00:00+01:00','2024-01-01T00:05:00Z','2024-01-01T00:00:00.000000001Z'])
def test_explicit_utc_native_hours_required(bad):
    data=bars();data['open_time']=data.open_time.astype(object);data.loc[0,'open_time']=bad
    with pytest.raises(ValueError):add_reference_features(data)


@pytest.mark.parametrize('mutation',['duplicate','reverse','source_segments','false_gap','raw_counter','bool_segment','null_segment','duplicate_columns','feature_collision','meta_collision','wrong_timeframe'])
def test_schema_and_source_segment_domain_fail_closed(mutation):
    data=bars()
    if mutation=='duplicate':data.loc[1,'open_time']=data.loc[0,'open_time']
    elif mutation=='reverse':data=data.iloc[::-1]
    elif mutation=='source_segments':data['segment_id']='raw-segment'
    elif mutation=='false_gap':data.loc[50:,'segment_id']=1
    elif mutation=='raw_counter':data.loc[50:,'open_time']+=pd.Timedelta(hours=1);data.loc[50:,'segment_id']=2
    elif mutation=='bool_segment':data['segment_id']=False
    elif mutation=='null_segment':data.loc[20,'segment_id']=np.nan
    elif mutation=='duplicate_columns':data=pd.concat([data,data[['open']]],axis=1)
    elif mutation=='feature_collision':data['ma']=1.
    elif mutation=='meta_collision':data['reference_known']=True
    elif mutation=='wrong_timeframe':data.attrs['bar_minutes']=15
    with pytest.raises(ValueError):add_reference_features(data)


@pytest.mark.parametrize('reference',['SMA','VWMA'])
def test_empty_stable_schema_and_attrs(reference):
    data=bars().iloc[:0];out=add_reference_features(data,reference)
    assert out.empty and set(FEATURE_COLUMNS+REFERENCE_COLUMNS).issubset(out)
    assert str(out.reference_available_at.dtype)=='datetime64[ns, UTC]'
    assert out.attrs['ma_kind']==reference and out.attrs['ma_length']==40 and out.attrs['bar_minutes']==60


@pytest.mark.parametrize('direction',[-1,1])
def test_bad_volume_does_not_change_default_sma_entry_qualification(direction):
    data=bars(60,constant_volume=10.)
    data[['open','high','low','close']]=[100.,101.,99.,100.]
    if direction==1:data.loc[40,['open','high','low','close']]=[99.,104.,98.9,103.8]
    else:data.loc[40,['open','high','low','close']]=[101.,101.1,96.,96.2]
    expected=make_entries(add_features(data,'SMA',40),{})
    assert len(expected)==1
    data.loc[40,'volume']=np.nan
    actual=make_entries(add_reference_features(data,'SMA'),{})
    assert actual.event_id.tolist()==expected.event_id.tolist()
    assert_frame_equal(actual.drop(columns='volume_ratio'),expected.drop(columns='volume_ratio'),check_exact=True)
    assert make_entries(add_reference_features(data,'VWMA'),{}).empty
