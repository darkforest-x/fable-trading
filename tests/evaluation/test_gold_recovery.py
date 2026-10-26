"""Native execution scenarios for fee recovery, confirmation clock and bounds."""
import unittest
import numpy as np
import pandas as pd
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy
from yoyo.layers.l3_backtest.nautilus_gold_recovery import run_gold_recovery
from yoyo.evaluation.gold_monday_recovery import prepare


class GoldRecoveryTests(unittest.TestCase):
    def run_case(self,bars,side=1,minutes=60):
        frame=pd.DataFrame(bars,columns=['open','high','low','close'],
            index=pd.date_range('2026-01-06T00:50Z',periods=len(bars),freq='5min'))
        if side<0:frame=pd.DataFrame(dict(open=200-frame.open,high=200-frame.low,low=200-frame.high,close=200-frame.close),index=frame.index)
        entries=pd.DataFrame([dict(trade_id='case',entry_i=0,expire_i=1,end_i=len(frame)-1,
            entry=100.,side=side,stop=100-side,risk=1.,target_price=100+side*2,near_edge=100+side,
            confirmation_minutes=minutes)])
        policy=ExitPolicy(trail_arm_r=1e12,opposite_hour_exit=False,round_trip_cost=0,
            target_from_entry=True,intrabar_path='OHLC',monotone_segments=True,
            fill_maker_fee=.0002,fill_taker_fee=.0005)
        return run_gold_recovery(frame,entries,policy,tick=.01,symbol='TEST',execution_minutes=5)

    def test_reclaimed_touch_close_protects_both_directions(self):
        bars=[[101]*4,[101,101,99.8,100.3],[100.3,101,100.1,100.5],[100.5,100.6,99.9,100.2],[100.2]*4]
        for side in [1,-1]:
            r,f,a=self.run_case(bars,side)
            self.assertEqual(r.iloc[0].exit_kind,'fee_stop')
            self.assertEqual(r.iloc[0].decision_i,2)
            self.assertGreaterEqual(r.iloc[0].net_r,-1e-9)
            self.assertEqual(len(f),2)

    def test_failed_reclaim_keeps_backstop_and_recovers_by_limit(self):
        bars=[[101]*4,[101,101,99.6,99.8],[99.8,99.9,99.5,99.7],[99.7,100.2,99.6,100.1],[100.1]*4]
        for side in [1,-1]:
            r,_,_=self.run_case(bars,side)
            self.assertEqual(r.iloc[0].exit_kind,'fee_recovery')
            self.assertGreaterEqual(r.iloc[0].net_r,-1e-9)
            self.assertLess(r.iloc[0].net_r,.011)

    def test_failed_reclaim_can_hit_backstop(self):
        r,_,_=self.run_case([[101]*4,[101,101,99.6,99.8],[99.8,99.9,99.5,99.7],[99.7,99.8,98.5,99],[99]*4])
        self.assertEqual(r.iloc[0].exit_kind,'backstop')
        self.assertAlmostEqual(r.iloc[0].net_r,-1.0695,places=6)

    def test_target_before_confirmation_and_no_postexit_extremes(self):
        r,_,_=self.run_case([[101]*4,[101,103,99.8,100.4],[100.4,103,100.3,102],[102,105,90,100]])
        self.assertEqual(r.iloc[0].exit_kind,'far_target')
        self.assertEqual(r.iloc[0].decision_status,'pending')
        self.assertEqual(r.iloc[0].mfe_r,2.)
        self.assertTrue(r.iloc[0].returned_to_range)

    def test_four_hour_clock_and_source_end_censor(self):
        r,_,_=self.run_case([[101]*4,[101,101,99.8,100.3],[100.3,101,100.1,100.5],[100.5]*4],minutes=240)
        self.assertEqual(r.iloc[0].decision_time,'2026-01-06 04:00:00+00:00')
        self.assertEqual(r.iloc[0].exit_kind,'censored')
        self.assertEqual(r.iloc[0].reason,0)

    def test_fee_band_does_not_install_crossed_stop(self):
        r,_,_=self.run_case([[101]*4,[101,101,99.8,100.01],[100.01,100.02,99.9,100.02],[100.02,100.2,99.9,100.1],[100.1]*4])
        self.assertEqual(r.iloc[0].decision_status,'fee_band_wait')
        self.assertEqual(r.iloc[0].exit_kind,'fee_recovery')
        self.assertGreaterEqual(r.iloc[0].net_r,0)

    def test_entry_matrix_keeps_same_admissions_for_both_clocks(self):
        index=pd.date_range('2026-10-19T04:00Z',periods=340,freq='5min')
        f=pd.DataFrame(dict(open=100.,high=102.,low=98.,close=100.,volume=1.),index=index)
        f.iloc[288,f.columns.get_loc('low')]=95.
        f.attrs['group']='crypto_5m'
        cfg=dict(tick=.1,seed=1,controls_per_signal=0,confirmation_minutes=[60,240])
        e,op,_=prepare(f,cfg)
        self.assertEqual(set(e.entry_mode),{'1618','edge'})
        self.assertEqual(set(e.confirmation_minutes),{60,240})
        for _,g in e.groupby(['entry_mode','side']):
            self.assertEqual(len(g),2)
            self.assertEqual(g.entry.nunique(),1)
            self.assertEqual(g.target_price.nunique(),1)
            self.assertTrue((g.side*(g.entry-g.stop)>0).all())
        # A later extreme cannot alter an already-closed Monday range.
        changed=f.copy();changed.iloc[-1,changed.columns.get_loc('high')]=1000
        later,_,_=prepare(changed,cfg)
        left=e.set_index('trade_id');right=later.set_index('trade_id').loc[left.index]
        np.testing.assert_allclose(left[['entry','stop','target_price']],right[['entry','stop','target_price']])


if __name__=='__main__':unittest.main()
