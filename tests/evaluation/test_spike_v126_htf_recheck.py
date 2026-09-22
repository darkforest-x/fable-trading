"""Behavioral checks for causal H1 recheck and independent joint-entry books."""
import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v126_htf_recheck import complete_bars, joint_permission, serial, pine_facts, structure_parents, bb_admission
from yoyo.evaluation import spike_v10_4_study as old
from yoyo.evaluation.spike_v9_htf_sma import confirmed_sma, side_gate


def sample(n=4800):
    rng=np.random.default_rng(922126)
    close=100*np.exp(np.cumsum(rng.normal(0,.004,n)))
    open_=np.r_[close[0],close[:-1]]
    return pd.DataFrame({'open':open_,'high':np.maximum(open_,close)*1.002,
        'low':np.minimum(open_,close)*.998,'close':close,'volume':rng.lognormal(3,.5,n)},
        index=pd.date_range('2025-01-01',periods=n,freq='5min',tz='UTC'))


def test_joint_direction_does_not_need_new_raw_signal_and_refuses_equality_unknown():
    known,passed=joint_permission([101,100,99,101],[100,100,100,np.nan])
    assert known.tolist()==[True,True,True,False]
    assert passed.tolist()==[True,False,False,False]
    assert not side_gate([101],[0],[100])[0]


def test_partial_higher_bucket_excluded_not_silently_aggregated():
    b=sample(36).drop(sample(36).index[13])
    h,partial=complete_bars(b,60)
    assert len(h)==2 and partial==1
    assert (h.index[1]-h.index[0])==pd.Timedelta(hours=2)


def test_permission_available_at_chart_open_not_new_h1_close():
    b=sample(900)
    ix=pd.date_range('2025-01-04 00:45',periods=2,freq='15min',tz='UTC')
    evidence=confirmed_sma(b,ix,60,(60,))
    assert evidence.htf_close_time.tolist()==[pd.Timestamp('2025-01-04T00:00Z'),pd.Timestamp('2025-01-04T01:00Z')]


def test_raw_v9_matches_old_contiguous_core_while_h1_only_changes_admission():
    b=sample();bars,_=complete_bars(b,15)
    new=pine_facts(bars,b,'ETH',.01)
    prior=old.v9_facts(bars,15,'ETH',.01)
    assert np.array_equal(new['side'],prior['side'])
    assert (new['side']!=0).any()
    expected=prior['v9'] & side_gate(bars.close,prior['side'],new['h1'].sma_60)
    assert np.array_equal(new['v9'],expected)
    assert (new['h1'].htf_close_time.dropna()<=new['h1'].htf_close_time.dropna().index).all()


def test_future_suffix_cannot_change_confirmations_or_h1_gate():
    b=sample();cut=b.index[3600]
    bars,_=complete_bars(b,15);a=pine_facts(bars,b,'ETH',.01)
    changed=b.copy();changed.loc[changed.index>=cut,['open','high','low','close']]*=2
    cbars,_=complete_bars(changed,15);z=pine_facts(cbars,changed,'ETH',.01)
    prefix=np.flatnonzero(bars.index+pd.Timedelta(minutes=15)<=cut)
    assert np.array_equal(a['side'][prefix],z['side'][prefix])
    assert np.array_equal(a['v9'][prefix],z['v9'][prefix])
    np.testing.assert_allclose(a['h1'].sma_60.iloc[prefix],z['h1'].sma_60.iloc[prefix],equal_nan=True)


def test_gap_clears_volume_array_and_ready_but_preserves_recursive_ma():
    b=sample();b=b.drop(b.index[1800:1812]);bars,_=complete_bars(b,15)
    f=pine_facts(bars,b,'ETH',.01)
    g=np.flatnonzero(f['gap'])[0]
    assert not f['ready'][g:g+12].any()
    assert f['frame'].rv.iloc[g+1:g+20].isna().all()
    assert np.isfinite(f['frame'].atr.iloc[g])


def test_gate_precedes_occupancy_and_replays_new_entries():
    events=[{'signal_i':i,'trade_key':str(i),'h1_known':True,'h1_pass':passed}
            for i,passed in [(1,False),(3,True),(8,True)]]
    def score(i):
        return 'closed',{'exit_i':i+5,'net_r':float(i),'censored':False}
    old_t,old_s=serial(events,'baseline',score,20)
    new_t,new_s=serial(events,'joint_h1_recheck',score,20)
    assert [r['signal_i'] for r in old_t]==[1,8]
    assert [r['signal_i'] for r in new_t]==[3,8]
    assert new_s[0]['status']=='rejected_direction'
    assert old_s[1]['status']=='skipped_in_position'
    assert old_t[-1]['net_r']==new_t[-1]['net_r']


def test_censored_position_does_not_create_additional_finished_trades():
    e=[{'signal_i':i,'trade_key':str(i),'h1_known':True,'h1_pass':True} for i in (1,3)]
    def score(i):return 'censored_boundary',{'exit_i':10,'censored':True}
    t,s=serial(e,'baseline',score,10)
    assert len(t)==1 and s[1]['status']=='skipped_in_position'


def test_structure_parent_resets_pending_but_preserves_consumed_parent():
    high,low=structure_parents([110,90,110,90,90], [True,True,True,True,False],
        [True,False,True,False,False], [105,np.nan,105,np.nan,np.nan],
        [95,np.nan,95,np.nan,np.nan], [False,False,True,False,False])
    np.testing.assert_allclose(high,[105,np.nan,105,105,np.nan],equal_nan=True)
    np.testing.assert_allclose(low,[95,np.nan,95,95,np.nan],equal_nan=True)


def test_bb200_requires_712_consecutive_bars_and_cannot_use_current_run():
    close=pd.Series(np.full(900,100.))
    admission=bb_admission(close,np.arange(1,901))
    assert not admission[:711].any()
    assert admission[711:].all()
    from yoyo.evaluation.spike_v7_fast import v7_diagnostics
    bars=sample(900)
    old_diag=v7_diagnostics(bars,data_gap=pd.Series(False,index=bars.index))
    expected=(old_diag.v7_ready & old_diag.prior_squeeze_run3).to_numpy()
    np.testing.assert_array_equal(bb_admission(bars.close,np.arange(1,901)),expected)
