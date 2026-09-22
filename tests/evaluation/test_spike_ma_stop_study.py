"""Causality of support-stop features and fixed random controls."""
from dataclasses import replace
import numpy as np
import pandas as pd
from yoyo.evaluation import spike_v10_4_study as source
from yoyo.evaluation import spike_ma_stop_study as study


def fixture():
    index=pd.date_range('2025-01-01',periods=1200,freq='5min',tz='UTC')
    close=100+np.arange(len(index))*.002
    base=pd.DataFrame({'open':close,'high':close+.2,'low':close-.2,'close':close,'volume':1.},index=index)
    chart,_=source.aggregate(base,15); chart['atr']=1.;chart['ready']=True
    p=source.prepared_arm(chart,np.zeros(len(chart),bool),np.zeros(len(chart),int),'test:15m',
            {'symbol':'TEST','timeframe':'15m'},15,.01)
    return base,p


def test_features_use_signal_prefix_and_completed_hour_at_chart_open():
    base,p=fixture();feature=study.features(base,p)
    stamp=pd.Timestamp('2025-01-04T00:00:00Z'); i=p.frame.index.get_loc(stamp)
    assert feature.htf_close_time.iloc[i]<=stamp
    altered=base.copy();altered.loc[altered.index>=stamp,'close']+=100
    # H1MA at the current bar open must ignore even the current chart bar.
    assert study.features(altered,p).sma_60.iloc[i]==feature.sma_60.iloc[i]
    changed=p.frame.copy();changed.loc[changed.index>stamp,'close']*=10
    changed_p=replace(p,frame=changed,close=changed.close.to_numpy())
    got=study.features(altered,changed_p)
    np.testing.assert_allclose(got.sma120.iloc[:i+1],feature.sma120.iloc[:i+1],equal_nan=True)
    assert feature.sma120.iloc[i]==p.frame.close.iloc[i-119:i+1].mean()


def test_local_sma_does_not_bridge_gaps():
    base,p=fixture();gap=p.gap.copy();gap[200]=True
    f=study.features(base,replace(p,gap=gap))
    assert np.isnan(f.sma120.iloc[318])
    assert np.isfinite(f.sma120.iloc[319])


def test_control_draw_shared_between_arms_and_not_retried_after_bad_outcome(monkeypatch):
    base,p=fixture();f=study.features(base,p)
    i=300;rows=[]
    for arm in study.ARMS:
        rows.append({'signal_i':i,'side':1,'arm':arm,'event_key':'same-event','censored':False})
    calls=[]
    def fake_score(p,j,side,apply):
        calls.append(j)
        return None
    monkeypatch.setattr(study,'score',fake_score)
    result=study.controls(p,f,pd.DataFrame(rows),{arm:lambda *args:None for arm in study.ARMS})
    assert len(calls)==4 and len(set(calls))==1
    assert result.control_signal_i.nunique()==1
    assert not result.matched.any()
    assert result.control_net_r.isna().all()


def test_periods_exclude_cross_split_and_censored_without_calling_them_losses():
    from yoyo.evaluation.spike_ma_stop_report import subset
    t=pd.DataFrame({'signal_bar_open':['2025-09-09T12:00Z','2025-09-09T12:00Z','2025-09-10T00:00Z','2025-09-10T12:00Z'],
                    'exit_time':['2025-09-09T18:00Z','2025-09-10T03:00Z','2025-09-10T10:00Z','2025-09-11T00:00Z'],
                    'censored':[False,False,False,True],'side':[1,1,1,1]})
    assert subset(t,'full','long').index.tolist()==[0,1,2]
    assert subset(t,'earlier','long').index.tolist()==[0]
    assert subset(t,'later','long').index.tolist()==[2]


def test_block_comparison_identical_arms_have_zero_difference_and_unit_p():
    from yoyo.evaluation.spike_ma_stop_report import uncertainty
    a=pd.DataFrame({'month':['2025-01','2025-01','2025-02','2025-03'],'net_r':[1.,-1.,3.,-2.]})
    result=uncertainty(a,a,'net_r')
    assert result['difference']==result['ci_low']==result['ci_high']==0.
    assert result['p']==1. and result['months']==3
