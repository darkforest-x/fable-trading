"""Causality and selection semantics for the SPIKE morphology adapter."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_ma_launch_gate as gate
from yoyo.evaluation import ma_dense_launch_v1_reference as rules

PACK = json.loads(Path('experiments/active/exp-ma-dense-launch-pine-20260923-v1/reference_pack.json').read_text())


def frame():
    rng = np.random.default_rng(523)
    close = 100+np.cumsum(rng.normal(0, .05, 350))
    opened = np.r_[close[0],close[:-1]]
    return pd.DataFrame({'open':opened,'high':np.maximum(opened,close)+.1,
                         'low':np.minimum(opened,close)-.1,'close':close,'volume':100},
                        index=pd.date_range('2024-01-01',periods=350,freq='15min',tz='UTC'))


def test_future_ohlc_cannot_change_evidence():
    raw = frame()
    before = gate.evaluate_gate(rules.add_features(raw),250,1,PACK)
    changed = raw.copy()
    changed.loc[changed.index[251:],['open','high','low','close']] *= 8
    after = gate.evaluate_gate(rules.add_features(changed),250,1,PACK)
    assert before['ma_known'] and len(before['cores'])==2
    assert before == after


def test_selection_precedes_stage2_without_fallback():
    cores=[{'stage1':True,'stage2':False,'distance':.2,'core_bars':4},
           {'stage1':True,'stage2':True,'distance':.3,'core_bars':5}]
    assert gate.select_stage1(cores,.5)['core_bars']==4
    cores[0]['distance']=.3
    assert gate.select_stage1(cores,.5)['core_bars']==4
    cores[0]['distance']=.51
    assert gate.select_stage1(cores,.5)['core_bars']==5
    cores[1]['stage1']=False
    assert gate.select_stage1(cores,.5) is None


def test_gap_inside_confirmation_rejects_both_geometries():
    enriched=rules.add_features(frame())
    enriched=enriched.drop(enriched.index[248])
    result=gate.evaluate_gate(enriched,249,-1,PACK)
    assert not result['ma_known'] and not result['ma_grade_a']


def test_local_slice_matches_full_frame_metrics():
    enriched=rules.add_features(frame())
    for side,direction in ((1,'LONG'),(-1,'SHORT')):
        result=gate.evaluate_gate(enriched,250,side,PACK)
        for core in result['cores']:
            expected=rules.evaluate(enriched,250,direction,core['core_bars'])
            assert core['metrics']==expected.metrics


def test_stage1_distance_not_conditioned_on_stage2(monkeypatch):
    enriched=rules.add_features(frame())
    metrics=rules.evaluate(enriched,250,'LONG',4).metrics
    monkeypatch.setattr(rules,'evaluate',lambda *a:rules.Decision(True,False,metrics,102,99))
    monkeypatch.setattr(gate,'stage1_distance',lambda *a:.2 if a[3]==4 else .3)
    seen=[]
    def full(f,i,d,n,p):
        seen.append(n)
        return {'quality_score':float('nan'),'grade_a':False}
    monkeypatch.setattr(rules,'evaluate_full',full)
    result=gate.evaluate_gate(enriched,250,1,PACK)
    assert seen==[4] and result['selected_core_bars']==4 and not result['ma_grade_a']
