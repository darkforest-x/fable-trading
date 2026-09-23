"""Check event matching and sparse-sample statistics without market outcomes."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_ma_launch_report as report
from yoyo.evaluation import spike_ma_launch_gate as gate
from yoyo.evaluation import ma_dense_launch_v1_reference as rules


def test_shared_control_rejoins_each_arm_and_rejects_boundary_control():
    split = pd.Timestamp('2025-09-10', tz='UTC')
    trades = pd.DataFrame({'event_key':['same','same','bad'],
                           'arm':['baseline','ma_hard','ma_hard'],
                           'censored':[False]*3,'net_bp':[25.,25.,900.]})
    controls = pd.DataFrame({'event_key':['same','bad'],'matched':[True,True],
                             'control_censored':[False,False],
                             'control_entry_time':['2025-10-01Z'.replace('Z','T00:00:00Z'),'2025-09-09T00:00:00Z'],
                             'control_exit_time':['2025-10-02T00:00:00Z']*2,
                             'control_net_return':[.001,.001]})
    got = report.matched(trades, controls, split, 'later')
    assert got.arm.tolist() == ['baseline','ma_hard']
    assert got.excess_bp.tolist() == [15.,15.]
    with pytest.raises(pd.errors.MergeError):
        report.matched(trades,pd.concat([controls,controls]),split,'later')


def test_time_boundary_and_censor_are_not_training_period_successes():
    frame=pd.DataFrame({'entry_time':['2025-09-09T00:00:00Z','2025-09-10T00:00:00Z','2025-09-08T00:00:00Z'],
                        'exit_time':['2025-09-10T00:00:00Z','2025-09-11T00:00:00Z','2025-09-09T00:00:00Z'],
                        'signal_bar_open':['2025-09-09T00:00:00Z']*3,
                        'net_return':[.01]*3,'gross_return':[.012]*3})
    got=report.add_period(frame,pd.Timestamp('2025-09-10',tz='UTC'))
    assert got.period.tolist()==['cross_split','later','earlier']
    got['censored']=[True,False,False];got['net_r']=[100.,1.,-1.]
    metrics=report.metrics(got)
    assert metrics['n']==2 and metrics['sum_net_r']==0 and metrics['net_ge3r']==0


def test_same_month_bootstrap_is_paired_not_independent():
    cfg={'start':'2025-01-01T00:00:00Z','split':'2025-01-01T00:00:00Z',
         'end':'2025-04-01T00:00:00Z','seed':1,'bootstrap_reps':1000}
    base=pd.DataFrame({'month':['2025-01','2025-02','2025-03'],'net_bp':[100.,-100.,0.]})
    treatment=base.copy();treatment.net_bp+=7
    got=report.month_delta(base,treatment,cfg)
    assert got['estimate_bp']==pytest.approx(7)
    assert got['low95']==pytest.approx(7) and got['high95']==pytest.approx(7)
    assert report.month_delta(base,treatment.iloc[:0],cfg)['valid_draws']==0


def test_week_block_flip_does_not_pretend_trades_are_independent():
    cfg={'seed':1,'permutation_reps':10000}
    one_week=pd.DataFrame({'week':['2025-01-01']*100,'excess_bp':[1.]*100})
    got=report.random_test(one_week,cfg)
    assert got['weeks']==1 and .45<got['p_raw']<.55
    assert report.random_test(one_week.iloc[:0],cfg)['p_raw']==1
    rows=[{'p_raw':.008},{'p_raw':.006}];report.holm_two(rows)
    assert [r['p_holm'] for r in rows]==[.012,.012]


def test_hard_arm_intentionally_has_no_reference_geometry_selection(monkeypatch):
    import json
    from pathlib import Path
    pack=json.loads(Path('experiments/active/exp-ma-dense-launch-pine-20260923-v1/reference_pack.json').read_text())
    frame=pd.DataFrame(index=pd.date_range('2025-01-01',periods=100,freq='15min',tz='UTC'))
    def evaluate(f,i,d,n):
        return rules.Decision(True,n==5,{'end_spread_atr':.1},102,99)
    monkeypatch.setattr(rules,'evaluate',evaluate)
    monkeypatch.setattr(gate,'stage1_distance',lambda *a:.2 if a[3]==4 else .3)
    seen=[]
    def full(f,i,d,n,p):
        seen.append(n);return {'quality_score':np.nan,'grade_a':False}
    monkeypatch.setattr(rules,'evaluate_full',full)
    result=gate.evaluate_gate(frame,99,1,pack)
    assert result['ma_hard'] and not result['ma_grade_a'] and seen==[4]
