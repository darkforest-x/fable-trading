"""Causal split boundaries and actual-outcome regression for the rebuilt models."""
import numpy as np
import pandas as pd
from yoyo.evaluation.btc_eth_repeat_data import bounds,candidates
from yoyo.evaluation.major_1618_v2_models import split,fit_model,score_model


def test_complete_week_purge_includes_fresh_recent_labels_only():
    rows=[]
    for week in ['2026-06-22','2026-06-29','2026-07-06','2026-09-21','2026-09-28','2026-10-05']:
        for side in [-1,1]:
            rows.append(dict(week=week,side=side,week_start=bounds(week)[0],expiry_dt=bounds(week)[2],complete=week!='2026-10-05'))
    d=pd.DataFrame(rows)
    old,cal,fresh,test=split(d,pd.Timestamp('2026-10-01',tz='America/New_York'))
    assert set(old.week)=={'2026-06-22'}
    assert set(cal.week)=={'2026-07-06','2026-09-21'}
    assert set(fresh.week)=={'2026-06-22','2026-06-29','2026-07-06','2026-09-21'}
    assert set(test.week)=={'2026-10-05'}
    assert all(g.side.nunique()==2 for _,g in test.groupby('week'))


def test_tree_net_objective_preserves_actual_timeout_profit():
    # All target labels fail, yet actual native-style timeout returns are positive.
    # The net model must learn netR, not silently substitute the target-hit label.
    d=pd.DataFrame(dict(f_x=np.arange(120,dtype=float),net_r=np.repeat(.2,120),
                        label=np.zeros(120,dtype=int),symbol='BTC',week=np.repeat([f'w{i}' for i in range(12)],10),
                        event_id=[str(i) for i in range(120)]))
    m=fit_model(d,['f_x'],'regressor')
    np.testing.assert_allclose(score_model(m,d,['f_x'],True),.2,atol=1e-7)


def test_model_inputs_do_not_consume_test_labels():
    rng=np.random.default_rng(8)
    d=pd.DataFrame(dict(f_x=rng.normal(size=120),net_r=rng.normal(size=120),label=np.tile([0,1],60),
                        symbol='ETH',week=np.repeat([f'w{i}' for i in range(12)],10),event_id=[str(i) for i in range(120)]))
    model=fit_model(d,['f_x'],'regressor')
    test=d.iloc[:10].copy();before=score_model(model,test,['f_x'],True)
    test['label']=1-test.label;test['net_r']=1e6
    np.testing.assert_array_equal(score_model(model,test,['f_x'],True),before)


def test_ranking_diagnostics_never_pool_different_model_scales():
    from yoyo.evaluation.major_1618_v2_evaluate import fold_rank_metrics
    data=pd.DataFrame(dict(fold=['q1','q1','q2','q2'],label=[0,1,0,1],
                           net_r=[-1,1,-1,1],score=[.8,.9,.1,.2],trade_id=['a','b','c','d']))
    folds,auc,profit,top=fold_rank_metrics(data)
    assert auc==profit==1.
    assert set(top.trade_id)=={'b','d'} and len(folds)==2
    shifted=data.copy();shifted.loc[shifted.fold.eq('q2'),'score']+=100
    _,other,_,other_top=fold_rank_metrics(shifted)
    assert other==auc and set(other_top.trade_id)==set(top.trade_id)
