"""Admission timing and cancellation boundaries for the frozen spike comparison."""
import unittest
import pandas as pd
from yoyo.evaluation.spike_close_execution import decision

CFG = dict(backstop_pct=18, be_offset_bp=17)


class CloseDecisionTests(unittest.TestCase):
    def setUp(self):
        self.p = pd.DataFrame([[102,103,90,102], [102,108,101,105], [105,107,99,103], [103]*4],
            columns=['open','high','low','close'], index=pd.date_range('2026-01-01',periods=4,freq='5min',tz='UTC'))
        self.source = dict(side=1,entry=100.,target_price=110.,decision_i=0,end_i=3,expire_i=3)

    def test_decision_reads_no_future_and_does_not_use_touch_wick(self):
        for variant in ('close_now','hybrid_retest'):
            a,_ = decision(self.source,self.p,.1,CFG,variant)
            changed=self.p.copy(); changed.iloc[1:]=1000
            b,_ = decision(self.source,changed,.1,CFG,variant)
            self.assertEqual(a,b)
            self.assertEqual(a['entry_i'],0)
            self.assertEqual(a['entry'],102 if variant=='close_now' else 100)

    def test_delay_uses_next_close_and_cancels_on_target_even_after_pullback(self):
        a,status=decision(self.source,self.p,.1,CFG,'delay_5m')
        self.assertEqual(status,'admitted'); self.assertEqual(a['entry_i'],1); self.assertEqual(a['entry'],105)
        changed=self.p.copy(); changed.loc[changed.index[1],'high']=111
        a,status=decision(self.source,changed,.1,CFG,'delay_5m')
        self.assertIsNone(a); self.assertEqual(status,'target_reached_while_waiting')

    def test_delay_cancels_backstop_and_source_gap(self):
        changed=self.p.copy(); changed.loc[changed.index[1],'low']=81
        self.assertEqual(decision(self.source,changed,.1,CFG,'delay_5m')[1],'backstop_reached_while_waiting')
        changed=self.p.copy(); changed.index=changed.index+pd.to_timedelta([0,5,5,5],unit='min')
        self.assertEqual(decision(self.source,changed,.1,CFG,'delay_5m')[1],'delay_source_gap')

    def test_short_mirror_keeps_original_level_for_protection(self):
        p=200-self.p; p[['high','low']]=p[['low','high']].to_numpy()
        a,_=decision(dict(self.source,side=-1,target_price=90.),p,.1,CFG,'close_now')
        self.assertEqual(a['entry'],98); self.assertEqual(a['reclaim_ref'],100); self.assertEqual(a['stop'],118)


if __name__=='__main__': unittest.main()
