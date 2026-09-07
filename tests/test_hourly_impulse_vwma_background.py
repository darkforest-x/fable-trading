"""Synthetic saved-context adapter parity, causal support and source identity."""
from copy import deepcopy

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal,assert_series_equal

from yoyo.data.hourly_impulse import add_features,make_entries
from yoyo.evaluation.hourly_impulse_k2_matching import build_matching_frame
from yoyo.evaluation.hourly_impulse_vwma import add_reference_features
from yoyo.evaluation.hourly_impulse_vwma_support import PARAMS
from yoyo.evaluation.hourly_impulse_background_support import build_support_graph
from yoyo.evaluation.hourly_impulse_vwma_background import (
    BUCKET_COLUMNS,INHERITED,REFERENCE_DEPENDENT,rebuild_matching,
)


def fixture(n=340,variable=True,gap=False,future_change_at=None):
    rng=np.random.default_rng(404)
    p=100+np.sin(np.arange(n)/5)
    o=p+rng.normal(0,.7,n);c=p+rng.normal(0,.7,n)
    hour=pd.DataFrame(dict(open_time=pd.date_range('2023-01-01T00:00Z',periods=n,freq='h'),
                          open=o,high=np.maximum(o,c)+.1,low=np.minimum(o,c)-.1,close=c,
                          volume=rng.uniform(1,100,n) if variable else 1.,segment_id=0))
    if future_change_at is not None:
        hour.loc[future_change_at:,['open','high','low','close']]*=1.2
        hour.loc[future_change_at:,'volume']*=7
    if gap:
        hour=hour.drop(index=130).reset_index(drop=True)
        hour.loc[130:,'segment_id']=1
    raw=pd.DataFrame({'open_time':pd.date_range(hour.open_time.iloc[0],hour.open_time.iloc[-1]+pd.Timedelta(hours=1),freq='5min')})
    if gap:
        raw=raw.loc[~raw.open_time.between(pd.Timestamp('2023-01-06T10:00Z'),pd.Timestamp('2023-01-06T10:55Z'))].reset_index(drop=True)
    raw['open']=100.
    raw['segment_id']=raw.open_time.diff().ne(pd.Timedelta(minutes=5)).cumsum()-1
    management=raw[['open_time','segment_id']].copy();management['ma_side']=1
    a=add_reference_features(hour,'SMA');b=add_reference_features(hour,'VWMA')
    old_entries=make_entries(add_features(hour,'SMA',40),PARAMS)
    h=build_matching_frame(raw,add_features(hour,'SMA',40),management,old_entries)
    m=make_entries(b,PARAMS).assign(fold='2023H1')
    assert len(m)>0
    return m,h,a,b


def repair_support(h):
    h['matching_support']=(h.vol_bucket.notna()&h.signal_atr.gt(0)&h.known_entry_open&h.entry_source_continuous&h.known_5m_valid&h.known_hourly_valid)
    h['candidate_eligible']=h.matching_support&~h.current_or_prior_cross_excluded&~h.actual_mother_decision_excluded


def test_uniform_volume_all_reference_and_support_parity():
    m,h,a,b=fixture(variable=False)
    out,info=rebuild_matching(m,h,a,b)
    assert_frame_equal(out[h.columns],h,check_dtype=False,check_exact=True)
    assert info['old_sma_all_feature_parity'] and info['causal_atr_bucket_parity']
    assert not info['allocation_performed'] and not info['outcomes_used']


def test_reference_changes_but_exit_sma_and_raw_source_remain_exact():
    m,h,a,b=fixture()
    out,info=rebuild_matching(m,h,a,b)
    assert_frame_equal(out[list(REFERENCE_DEPENDENT)],b[list(REFERENCE_DEPENDENT)],check_exact=True)
    assert not out.ma.equals(h.ma)
    assert_frame_equal(out[list(INHERITED+BUCKET_COLUMNS)],h[list(INHERITED+BUCKET_COLUMNS)],check_exact=True)
    for key in ('known_5m_colour','known_5m_available','known_5m_valid','management_source_segment_id'):
        assert_series_equal(out['exit_sma5_'+key],h[key],check_names=False,check_exact=True)
    assert_series_equal(out.inherited_sma_ma,h.ma,check_names=False)
    assert out.actual_mother_decision_excluded.eq(out.decision_time.isin(m.decision_time)).all()
    assert not out.actual_mother_decision_excluded.equals(h.actual_mother_decision_excluded)
    assert info['matching_keys']==['month','utc_6h_bucket','vol_bucket']


def test_current_and_only_previous_hour_cross_recomputed():
    m,h,a,b=fixture()
    out,_=rebuild_matching(m,h,a,b)
    cross=((b.open<b.ma)&(b.close>b.ma))|((b.open>b.ma)&(b.close<b.ma))
    now=set(out.loc[cross,'decision_time'])
    expect=out.decision_time.isin(now|{t+pd.Timedelta(hours=1) for t in now})
    assert_series_equal(out.raw_strict_body_cross,cross,check_names=False)
    assert_series_equal(out.current_or_prior_cross_excluded,expect,check_names=False)
    assert not out.raw_strict_body_cross.equals(h.raw_strict_body_cross)


def test_old_mother_list_replaced_not_unioned():
    m,h,a,b=fixture()
    old_only=h.actual_mother_decision_excluded&~h.decision_time.isin(m.decision_time)
    assert old_only.any()
    out,_=rebuild_matching(m,h,a,b)
    assert not out.loc[old_only,'actual_mother_decision_excluded'].any()
    assert out.loc[out.decision_time.isin(m.decision_time),'actual_mother_decision_excluded'].all()


def test_independent_indexes_and_larger_old_right_tail():
    m,h,a,b=fixture(n=400)
    a,b=a.iloc[:340].copy(),b.iloc[:340].copy()
    m=m.loc[m.signal_time.le(a.open_time.iloc[-1])].copy()
    h.index=np.arange(len(h))*7;a.index=np.arange(len(a))*11;b.index=np.arange(len(b))*13
    out,info=rebuild_matching(m,h,a,b)
    assert len(out)==340 and info['old_right_tail_rows_excluded']==60
    assert out.open_time.tolist()==a.open_time.tolist()


def test_gaps_reset_hourly_features_without_comparing_raw_segment_domain():
    m,h,a,b=fixture(n=450,gap=True)
    for key in ('source_segment_id','entry_source_segment_id','management_source_segment_id'):
        h[key]=h[key].map(lambda x: 'raw-zero' if x==0 else 'raw-two')
    out,_=rebuild_matching(m,h,a,b)
    assert out.segment_id.iloc[130]==1 and out.source_segment_id.iloc[130]=='raw-two'
    assert not out.known_hourly_valid.iloc[130:172].any()
    assert out.open_time.equals(a.open_time)


@pytest.mark.parametrize('missing',['old','sma','vwma'])
def test_internal_missing_hour_never_inner_joined_away(missing):
    m,h,a,b=fixture()
    if missing=='old':h=h.drop(index=200)
    elif missing=='sma':a=a.drop(index=200)
    else:b=b.drop(index=200)
    with pytest.raises(ValueError):rebuild_matching(m,h,a,b)


@pytest.mark.parametrize('which',['old','sma','vwma'])
@pytest.mark.parametrize('bad',['duplicate','reverse','naive','subhour','numeric'])
def test_invalid_clock_input_rejected(which,bad):
    m,h,a,b=fixture();frame={'old':h,'sma':a,'vwma':b}[which]
    if bad=='duplicate':frame.loc[201,'open_time']=frame.loc[200,'open_time']
    elif bad=='reverse':frame.loc[:,'open_time']=frame.open_time.iloc[::-1].to_numpy()
    else:
        frame['open_time']=frame.open_time.astype(object)
        frame.loc[200,'open_time']={'naive':'2023-01-09 08:00:00','subhour':'2023-01-09T08:05Z','numeric':1673222400000}[bad]
    with pytest.raises(ValueError):rebuild_matching(m,h,a,b)


@pytest.mark.parametrize('column',['ma','ma_side','ma_slope_atr','cross_count24','atr','volume_ratio','body_ratio','signal_atr'])
def test_old_sma_feature_drift_cannot_be_silently_relabelled_vwma(column):
    m,h,a,b=fixture();h.loc[210,column]+=1
    with pytest.raises(ValueError):rebuild_matching(m,h,a,b)


@pytest.mark.parametrize('column',['atr_fraction','atr_tercile_low','atr_tercile_high','vol_bucket'])
def test_bucket_recomputed_and_bad_bucket_rejected(column):
    m,h,a,b=fixture();h.loc[210,column]+=.01 if column!='vol_bucket' else 1
    repair_support(h)
    with pytest.raises(ValueError):rebuild_matching(m,h,a,b)


@pytest.mark.parametrize('flag',['known_5m_valid','known_hourly_valid','known_entry_open','entry_source_continuous'])
def test_inherited_unknown_source_cannot_become_eligible(flag):
    m,h,a,b=fixture();h.loc[210,flag]=False;repair_support(h)
    out,_=rebuild_matching(m,h,a,b)
    assert not out.matching_support.iloc[210] and not out.candidate_eligible.iloc[210]
    assert out[flag].iloc[210]==False


def test_sma5_colour_can_be_opposite_without_becoming_extra_exact_gate():
    m,h,a,b=fixture();h.loc[200:,'known_5m_colour']=-1
    out,_=rebuild_matching(m,h,a,b)
    assert out.known_5m_colour.iloc[210]==-1
    assert out.matching_support.iloc[210]


def test_zero_volume_only_removes_vwma_hourly_availability():
    m,h,a,b=fixture()
    raw=a[['open_time','open','high','low','close','volume','segment_id']].copy();raw.loc[180:220,'volume']=0.
    aa=add_reference_features(raw,'SMA');bb=add_reference_features(raw,'VWMA')
    for key in ('volume','volume_ratio'):
        h[key]=aa[key]
    m=make_entries(bb,PARAMS).assign(fold='2023H1')
    out,_=rebuild_matching(m,h,aa,bb)
    assert out.inherited_sma_known_hourly_valid.iloc[219]
    assert not out.known_hourly_valid.iloc[219] and not out.matching_support.iloc[219]
    assert out.reference_reason.iloc[219]=='zero_volume_sum'


@pytest.mark.parametrize('column',['signal_open','signal_close','signal_atr','initial_stop','ma','ma_slope_atr','cross_count24','extension_atr'])
def test_new_mother_all_owned_entry_fields_verified(column):
    m,h,a,b=fixture();m.loc[m[column].dropna().index[0],column]+=1
    with pytest.raises(ValueError):rebuild_matching(m,h,a,b)


@pytest.mark.parametrize('mutation',['duplicate','bool_direction','bad_id','bad_fold','wrong_known_fold','clock','outcome'])
def test_mother_schema_identity_rejected(mutation):
    m,h,a,b=fixture()
    if mutation=='duplicate':m=pd.concat([m,m.iloc[:1]],ignore_index=True)
    elif mutation=='bool_direction':m['direction']=True
    elif mutation=='bad_id':m.loc[m.index[0],'event_id']='foreign'
    elif mutation=='bad_fold':m['fold']='unknown'
    elif mutation=='wrong_known_fold':m['fold']='2023H2'
    elif mutation=='clock':m.loc[m.index[0],'decision_time']+=pd.Timedelta(hours=1)
    else:m['net_return']=0.
    with pytest.raises(ValueError):rebuild_matching(m,h,a,b)


def test_inputs_not_mutated_and_future_suffix_cannot_change_prior_frame():
    m,h,a,b=fixture(n=400)
    copies=[deepcopy(f) for f in (m,h,a,b)]
    out,_=rebuild_matching(m,h,a,b)
    for source,old in zip((m,h,a,b),copies):assert_frame_equal(source,old)
    prefix=320
    mm=m.loc[m.signal_time.le(a.open_time.iloc[prefix-1])]
    early,_=rebuild_matching(mm,h,a.iloc[:prefix],b.iloc[:prefix])
    assert_frame_equal(out.iloc[:prefix],early,check_exact=True)


def test_actual_future_price_volume_mutation_cannot_change_prior_adaptation():
    first=fixture(n=400)
    changed=fixture(n=400,future_change_at=320)
    out,_=rebuild_matching(*first)
    future,_=rebuild_matching(*changed)
    assert_frame_equal(out.iloc[:320],future.iloc[:320],check_exact=True)
    assert not out.ma.iloc[320:].equals(future.ma.iloc[320:])


def test_empty_mothers_preserved_but_empty_source_is_error():
    m,h,a,b=fixture()
    out,info=rebuild_matching(m.iloc[:0],h,a,b)
    assert info['mothers']==0 and not out.actual_mother_decision_excluded.any()
    with pytest.raises(ValueError):rebuild_matching(m.iloc[:0],h,a.iloc[:0],b.iloc[:0])


def test_nonpositive_next_open_risk_retains_mother_for_graph_unknown_reason():
    m,h,a,b=fixture()
    mother=m.loc[m.signal_time>=a.open_time.iloc[200]].iloc[0]
    h.loc[h.decision_time.eq(mother.decision_time),'entry_open']=mother.initial_stop
    out,info=rebuild_matching(m,h,a,b)
    graph=build_support_graph(m,out)
    assert len(graph['original_mothers'])==len(m)==info['mothers']
    assert graph['mother_support'].set_index('event_id').loc[mother.event_id,'support_reason']=='invalid_mother_risk'
    assert graph['eligible_edges'].candidate_id.str.endswith('+00:00').all()
