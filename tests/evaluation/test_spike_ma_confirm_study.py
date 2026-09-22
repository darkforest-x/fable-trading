"""Shared-draw, causal feature and downside reporting contracts for MA exits."""
import numpy as np
import pandas as pd
from yoyo.evaluation import spike_ma_confirm_study as study
from yoyo.evaluation import spike_ma_confirm_report as report
def fixture():
    index=pd.date_range('2025-01-01',periods=1200,freq='5min',tz='UTC')
    close=100+np.arange(len(index))*.002
    base=pd.DataFrame({'open':close,'high':close+.2,'low':close-.2,'close':close,'volume':1.},index=index)
    chart,_=study.source.aggregate(base,15);chart['atr']=1.;chart['ready']=True
    p=study.source.prepared_arm(chart,np.zeros(len(chart),bool),np.zeros(len(chart),int),
        'test:15m',{'symbol':'TEST','timeframe':'15m'},15,.01)
    return base,p


def test_predeclared_edges_change_one_rule_axis_only():
    assert len(study.ARMS)==8 and len(set(study.ARMS))==8
    for arm,base in study.COMPARATORS.items():
        a,b=arm.split('__'),base.split('__')
        assert sum(x!=y for x,y in zip(a,b))==1


def test_controls_share_draw_and_keep_failure_without_redraw(monkeypatch):
    base,p=fixture();feature=study.prior.features(base,p);i=300
    trades=pd.DataFrame([{'signal_i':i,'side':1,'arm':a,'event_key':'one-event','censored':False} for a in study.ARMS])
    calls=[]
    def fake_score(p,f,j,side,arm): calls.append(j);return None
    monkeypatch.setattr(study,'score',fake_score)
    result=study.controls(p,feature,trades)
    assert len(calls)==8 and len(set(calls))==1
    assert result.control_signal_i.nunique()==1
    assert not result.matched.any() and result.control_net_r.isna().all()


def test_risk_multiplier_does_not_claim_body_loss_is_capped():
    t=pd.DataFrame({'net_r':[-3.,1.,2.],'gross_r':[-2.9,1.1,2.1],
      'net_original_r':[-6.,2.,4.],'gross_return':[-.058,.022,.042],
      'net_return':[-.06,.02,.04],'initial_risk_frac':[.02]*3,'position_multiplier':[.5]*3,
      'entry_time':['2025-01-01T00:00Z']*3,'exit_time':['2025-01-01T01:00Z']*3,
      'exit_reason':['ma_body_next_open','opposite_v6_next_open','tp2']})
    got=report.stats(t)
    assert got['worst_net_r']==-3 and got['net_loss_beyond_2r']==1
    assert got['gross_loss_beyond_1r']==1 and got['net_r_cvar05']==-3
    assert got['ma_confirm_exits']==got['tp2_exits']==1
    assert got['mean_holding_hours']==1


def test_score_uses_transformed_initial_risk_and_higher_series(monkeypatch):
    base,p=fixture();feature=study.prior.features(base,p);seen={}
    def fake_replay(p,row,*,policy,higher):
        seen.update(row=row.copy(),policy=policy,higher=higher.copy());return {'censored':True}
    monkeypatch.setattr(study.kernel,'replay_fixed',fake_replay)
    i=300;arm='htf_body__tp2'
    result=study.score(p,feature,i,1,arm)
    original=study.prior.initial(p,i,1)
    assert result['censored'] and seen['row'].initial_risk>=original['initial_risk']
    np.testing.assert_equal(seen['higher'],feature.sma_60.to_numpy(float))
    assert seen['policy']==arm


def test_exact_month_permutation_exposes_small_sample_resolution():
    a=pd.DataFrame({'month':[f'2025-{i:02}' for i in range(1,9)],'net_r':[1.]*8})
    b=a.copy();b['net_r']=0.
    got=report.uncertainty(a,b,'net_r')
    assert got['p']==got['p_resolution']==1/256
    assert got['permutation_method']=='exact_month_signs'
    assert got['p_resolution']*30>.01
