"""Causal cohort, matching and end-to-end synthetic research checks."""
from dataclasses import asdict
import numpy as np
import pandas as pd
import pytest
from yoyo.data.altcoin_features import build_altcoin_features
import yoyo.evaluation.altcoin_trend_research as r


def test_weekly_rank_excludes_current_week_and_respects_sunday_close():
    idx=pd.date_range('2025-01-01',periods=1200,freq='1h',tz='UTC')
    monday=idx[(idx.dayofweek==0)&(idx.hour==0)&(np.arange(len(idx))>700)][0]
    pos=idx.get_loc(monday)
    def pool(shock=None):
        result={}
        for j in range(10):
            width=.5+j*.00001
            b=pd.DataFrame(dict(open=100.,close=100.,high=100+width,low=100-width,volume=10.),index=idx)
            if j==0 and shock is not None:
                b.iloc[pos+shock,b.columns.get_loc('high')]=1000.
            result[f'C{j}']=build_altcoin_features(b)
        return result
    normal=r.weekly_membership(pool());later=r.weekly_membership(pool(0));sunday=r.weekly_membership(pool(-1))
    def chosen(f):return set(f.loc[(f.week==monday)&f.high_vol,'symbol'])
    assert chosen(normal)==chosen(later)=={'C7','C8','C9'}
    assert 'C0' in chosen(sunday)
    assert (sunday.loc[sunday.week==monday,'eligible_count']==10).all()


def test_ties_are_symbol_stable_and_unwarmed_coins_do_not_rank():
    idx=pd.date_range('2025-01-01',periods=1000,freq='1h',tz='UTC')
    f=pd.DataFrame(dict(prior7d_atr_pct_mean=.02,ready=True),index=idx)
    pool={f'C{i:02d}':f.copy() for i in range(10)}
    pool['NEW']=f.iloc[-100:].copy();pool['BTC']=f*1;pool['SOPH']=f*1
    m=r.weekly_membership(pool)
    assert set(m.symbol)==set(pool)-{'NEW','BTC','SOPH'}
    assert set(m.loc[m.high_vol,'symbol'])=={'C00','C01','C02'}
    assert len(r.weekly_membership({'X':f}))>0
    assert not r.weekly_membership({'X':f}).high_vol.any()


def test_matching_is_same_clock_cohort_side_and_reproducible():
    idx=pd.date_range('2025-01-01',periods=1400,freq='1h',tz='UTC')
    f=pd.DataFrame(dict(atr_pct=.01,md=1.,release_side=0),index=idx)
    f.iloc[::2,f.columns.get_loc('md')]=-1
    for i in [700,710,721,1100]: f.iloc[i,f.columns.get_loc('release_side')]=np.sign(f.md.iloc[i])
    eligible=np.zeros(len(f),bool);eligible[600:900]=True
    a=r.matched_controls(f,eligible,seed=3)
    assert a==r.matched_controls(f,eligible,seed=3)
    used=[]
    for i,controls in a.items():
        assert len(controls)==3
        for j in controls:
            assert eligible[j] and not f.release_side.iloc[j]
            assert idx[j].month==idx[i].month
            assert np.sign(f.md.iloc[j])==f.release_side.iloc[i]
        used+=controls
    assert len(used)==len(set(used))
    assert 1100 not in a


def test_holm_monotonic_and_missing():
    result=r.holm([.04,.001,np.nan,.02])
    assert np.allclose(result[[0,1,3]],[.08,.004,.06])
    assert np.isnan(result[2])


def test_parameter_family_is_only_one_change_from_baseline():
    base=asdict(r.PARAMS['base'])
    assert len(r.PARAMS)==11
    for key,value in r.PARAMS.items():
        if key!='base': assert sum(base[k]!=v for k,v in asdict(value).items())==1
    assert len(r.ARMS)==19


def test_selection_never_uses_recent_outcomes_and_keeps_no_pass(tmp_path):
    rows=[]
    for arm in ['base','ma21']:
        for fold in ['development','validation']:
            rows.append(dict(minutes=60,cohort='high_vol',arm=arm,fold=fold,n=100,symbols=20,matched_n=100,matched_symbols=20,mean_net_bp=20,excess_bp=-1,portfolio_net_pct=10,mdd_pct=-5))
    frame=pd.DataFrame(rows);frame.to_csv(tmp_path/'summary.csv',index=False)
    lock=r.lock_selection(frame,tmp_path)
    assert lock['selections'][0]['selected_arm'] is None
    frame.loc[frame.arm=='ma21','excess_bp']=1
    frame.to_csv(tmp_path/'summary.csv',index=False)
    assert r.lock_selection(frame,tmp_path)['selections'][0]['selected_arm']=='ma21'


def test_synthetic_end_to_end_persists_scalar_diagnostics_and_equal_control_starts(tmp_path, monkeypatch):
    idx=pd.date_range('2025-01-01',periods=1000,freq='1h',tz='UTC')
    b=pd.DataFrame(dict(open=100.,high=101.,low=99.,close=100.,volume=10.),index=idx)
    f=build_altcoin_features(b)
    for col in b: f[col]=b[col]
    f['release_side']=0; f['md']=.5
    f.iloc[700,f.columns.get_loc('release_side')]=1
    f.iloc[750:,f.columns.get_loc('md')]=0
    f['atr']=1.;f['sma60']=99.;f['ready']=True
    feature=tmp_path/'features.pkl.gz';f.to_pickle(feature,compression='gzip')
    monkeypatch.setattr(r,'ARMS',[a for a in r.ARMS if a['arm'] in ('base','exit_fixed3r')])
    members=pd.DataFrame(columns=['week','symbol','high_vol'])
    result=r.evaluate_symbol('SOPH',{'cohort':'owner_illustration'},60,{'base':feature},members,
                             [('validation','2025-01-01','2026-01-01')],tmp_path)
    events=pd.read_csv(result['event_file'])
    assert len(events)==2
    assert events.groupby('signal_i').control_indexes.nunique().max()==1
    assert (tmp_path/'SOPH_60'/'diagnostics.json').exists()
    summary=r.summarize(tmp_path,[{'symbol':'SOPH'}],[60],[('validation','2025-01-01','2026-01-01')])
    assert len(summary)==2
    assert (summary.matched_n==1).all()
    assert (summary.capital_sleeves==2).all()
    assert np.isfinite(summary.portfolio_net_pct).all()


def test_selection_hash_is_required_and_audit_arm_list_is_explicit(tmp_path):
    (tmp_path/'summary.csv').write_text('n\n1\n')
    r.dump_json(tmp_path/'selection_lock.json',{})
    with pytest.raises(ValueError): r.read_selection(tmp_path,[60])
