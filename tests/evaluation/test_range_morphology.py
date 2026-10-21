"""Feature causality, reflection symmetry and descriptive-statistic contracts."""
import json
import unittest

import numpy as np
import pandas as pd

from yoyo.evaluation.range_morphology import (
    extract_features,mask_for,wilson,association,cluster_design,CONFIG,
)


def example():
    close=100+np.sin(np.arange(60))
    q=pd.DataFrame(dict(open=close-.2,high=close+2,low=close-2,close=close),
        index=pd.date_range('2026-02-03',periods=60,freq='15min',tz='UTC'))
    q.iloc[30]=[103,105,96,101]
    q.iloc[31]=[101,104,100,103]
    matches=pd.DataFrame([dict(pair_id=7,touch_i=30,signal_i=29,entry_i=31,
        side=1,level=98.5,key='0.618:up',entry=103.,stop=96.)])
    return q,matches


class MorphologyTests(unittest.TestCase):
    def test_raw_wick_geometry_and_roles(self):
        q,m=example();r=extract_features(q,m,.1).iloc[0]
        self.assertEqual(r.wick_points,5)
        self.assertEqual(r.body_points,2)
        self.assertAlmostEqual(r.wick_share,5/9)
        self.assertAlmostEqual(r.penetration,.5)
        self.assertAlmostEqual(r.signed_body_share,-2/9)
        self.assertEqual(r.touch_entry_lag,1)
        self.assertEqual(r.touch_arrow_offset,1)
        self.assertAlmostEqual(r.followthrough_atr,2/r.atr_before_touch)

    def test_future_prefix_and_future_mutation_invariance(self):
        q,m=example();expected=extract_features(q,m,.1)
        pd.testing.assert_frame_equal(extract_features(q.iloc[:32],m,.1),expected)
        changed=q.copy();changed.iloc[32:]*=100
        pd.testing.assert_frame_equal(extract_features(changed,m,.1),expected)

    def test_touch_not_in_its_own_atr_denominator(self):
        q,m=example();old=extract_features(q,m,.1)
        q.iloc[30,q.columns.get_loc('high')]+=100
        new=extract_features(q,m,.1)
        self.assertEqual(old.atr_before_touch.iloc[0],new.atr_before_touch.iloc[0])

    def test_direction_mirror(self):
        q,m=example();first=extract_features(q,m,.1)
        mirror=pd.DataFrame(dict(open=300-q.open,high=300-q.low,low=300-q.high,close=300-q.close),index=q.index)
        m=m.copy();m[['entry','stop','level']]=300-m[['entry','stop','level']];m['side']=-1
        second=extract_features(mirror,m,.1)
        geometry=['wick_points','body_points','atr_before_touch','wick_share','wick_body','wick_atr',
            'penetration','range_atr','signed_body_share','close_rejection','sweep5','sweep20',
            'approach8_atr','efficiency20','compression5_20','rejections5','directional_engulf',
            'followthrough_atr','arrow_body_share','body_touches20']
        np.testing.assert_allclose(first[geometry],second[geometry],atol=1e-10,rtol=0)

    def test_invalid_future_arrow_rejected(self):
        q,m=example();m.loc[0,'signal_i']=32
        with self.assertRaises(AssertionError): extract_features(q,m,.1)

    def test_all_configured_features_and_complement_partition(self):
        q,m=example();d=extract_features(q,m,.1);cfg=json.loads(CONFIG.read_text())
        for cut in cfg['hypotheses']+cfg['context_cuts']:
            yes=mask_for(d,cut)
            self.assertEqual(len(d[yes])+len(d[~yes]),len(d))

    def test_wilson_known_example(self):
        from scipy.stats import binomtest
        lo,hi=wilson(20,75)
        reference=binomtest(20,75).proportion_ci(method='wilson')
        self.assertAlmostEqual(lo,reference.low,places=12)
        self.assertAlmostEqual(hi,reference.high,places=12)

    def test_cluster_test_detects_perfect_effect_and_ignores_constant(self):
        cfg=json.loads(CONFIG.read_text());cfg.update(wild_score_draws=999,bootstrap_weeks=500)
        n=40;x=np.tile([0,1],20)
        d=pd.DataFrame(dict(net_win=x,week=np.repeat(np.arange(20),2),side=np.tile([-1,-1,1,1],10),
            risk_bp=np.linspace(10,50,n),entry_time=pd.date_range('2026-01-01',periods=n,freq='7D',tz='UTC').astype(str)))
        design=cluster_design(d,cfg);result=association(d,x,design,cfg)
        self.assertAlmostEqual(result['delta_win'],1)
        self.assertAlmostEqual(result['adjusted_delta'],1)
        self.assertLess(result['wild_p'],.01)
        self.assertEqual(result['bootstrap95'],[1,1])
        self.assertFalse(association(d,np.ones(n),design,cfg)['inferable'])


if __name__=='__main__': unittest.main()
