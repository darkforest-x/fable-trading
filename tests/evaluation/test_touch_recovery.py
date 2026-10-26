"""Native fill tests for no-stop touch-close recovery and three weighted legs."""
import unittest
import numpy as np
import pandas as pd
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy
from yoyo.layers.l3_backtest.nautilus_touch_recovery import run_touch_recovery
from yoyo.evaluation.monday_12h_recovery import prepare,parents,equity

class TouchRecoveryTests(unittest.TestCase):
    def run_case(self,bars,side=1):
        f=pd.DataFrame(bars,columns=['open','high','low','close'],index=pd.date_range('2026-01-06T11:50Z',periods=len(bars),freq='5min'))
        if side<0:f=pd.DataFrame(dict(open=200-f.open,high=200-f.low,low=200-f.high,close=200-f.close),index=f.index)
        e=pd.DataFrame([dict(trade_id=f'case:{k}',position_key='case',event_id='case',symbol='TEST',week='2026-01-05',
            population='signal',control_id=0,entry_i=0,expire_i=1,end_i=len(f)-1,entry=100.,side=side,
            stop=np.nan,risk=100.,target_price=100+side*t,near_edge=100+side*.5,reclaim_minutes=720,leg=k,weight=w)
            for k,(t,w) in enumerate(zip([2,3,4],[.7,.15,.15]),1)])
        p=ExitPolicy(trail_arm_r=1e12,opposite_hour_exit=False,round_trip_cost=0,target_from_entry=True,
            reclaim_next_bar=True,breakeven_includes_cost=False,intrabar_path='OHLC',monotone_segments=True,
            fill_maker_fee=.0002,fill_taker_fee=.0005)
        r,fills,a=run_touch_recovery(f,e,p,tick=.01,symbol='TEST',execution_minutes=5)
        r['weighted_net_r']=r.net_r*100*r.weight
        return r,fills,a,f

    def test_touch_close_stops_long_and_short(self):
        bars=[[101]*4,[101,101,99.8,100.3],[100.3,101,100.1,100.5],[100.5,100.6,99.9,100.2],[100.2]*4]
        for side in [1,-1]:
            r,_,a,_=self.run_case(bars,side)
            self.assertEqual(set(r.exit_kind),{'fee_stop'});self.assertTrue(r.net_r.ge(0).all())
            self.assertEqual(set(r.decision_i),{2});self.assertEqual(set(r.decision_time),{'2026-01-06 12:00:00+00:00'})
            self.assertEqual(a['native_fixed_target_accounting_checked'],3)

    def test_no_initial_stop_persists_then_recovers(self):
        bars=[[101]*4,[101,101,99.6,99.8],[99.8,99.9,90,91],[91,99,80,98],[98,100.2,97,100.1],[100.1]*4]
        for side in [1,-1]:
            r,_,_,f=self.run_case(bars,side)
            self.assertEqual(set(r.exit_kind),{'fee_recovery'});self.assertTrue(r.mae_fixed_r.le(-20).all())
            self.assertTrue(parents(r).iloc[0].breakeven_only);self.assertAlmostEqual(equity(r,f)[-1],r.weighted_net_r.sum())

    def test_open_loss_is_censored_not_realized(self):
        r,_,_,_=self.run_case([[101]*4,[101,101,99.6,99.8],[99.8,99.9,90,91],[91,95,80,85]])
        p=parents(r).iloc[0]
        self.assertEqual(set(r.reason),{0});self.assertFalse(p.closed);self.assertEqual(p.realized_r,0)
        self.assertLess(p.open_mark_r,-15);self.assertEqual(p.open_weight,1.)

    def test_partial_targets_do_not_force_sibling_stop(self):
        r,_,_,_=self.run_case([[101]*4,[101,101,99.8,100.3],[100.3,102.5,100.1,102.1],[102.1,103.5,101,103],[103,104.5,102,104],[104]*4])
        p=parents(r).iloc[0]
        self.assertEqual(set(r.exit_kind),{'target'});self.assertTrue(p.tp1 and p.tp2 and p.tp3)
        gross=.7*2+.15*3+.15*4
        fees=.0002*(100+.7*102+.15*103+.15*104)
        self.assertAlmostEqual(p.total_mark_r,gross-fees,places=6)

    def test_target_before_close_and_rest_breakeven(self):
        r,_,_,_=self.run_case([[101]*4,[101,101,99.8,100.3],[100.3,102.5,100.1,100.5],[100.5,100.6,99.9,100.2],[100.2]*4])
        p=parents(r).iloc[0]
        self.assertTrue(p.partial_tp_then_be);self.assertTrue(p.tp1);self.assertFalse(p.breakeven_only)
        self.assertEqual(r[r.leg.eq(1)].iloc[0].decision_status,'pending')

    def test_fee_band_and_equal_quote(self):
        for end,status in [(100.02,'fee_band_wait'),(100.08,'reclaimed')]:
            r,_,_,_=self.run_case([[101]*4,[101,101,99.8,100.01],[100.01,100.08,99.9,end],[end,100.2,99.9,100.1],[100.1]*4])
            self.assertEqual(set(r.decision_status),{status});self.assertTrue(r.net_r.ge(0).all())

    def test_geometry_mirror_and_causal_monday(self):
        f=pd.DataFrame(dict(open=100.,high=102.,low=98.,close=100.,volume=1.),index=pd.date_range('2026-10-19T04:00Z',periods=340,freq='5min'))
        f.iloc[288,f.columns.get_loc('low')]=95.;f.iloc[290,f.columns.get_loc('high')]=105.;f.attrs['group']='crypto_5m'
        cfg=dict(ticks={'TEST':.01},seed=1,controls_per_signal=0,weights=[.7,.15,.15])
        e,_,_=prepare(f,cfg,'TEST')
        self.assertEqual(len(e),6);self.assertTrue(e.stop.isna().all());self.assertTrue(e.risk.eq(e.entry).all())
        np.testing.assert_allclose(e[e.side.eq(1)].target_price,[100.47,102,104.47])
        np.testing.assert_allclose(e[e.side.eq(-1)].target_price,[99.53,98,95.53])

if __name__=='__main__':unittest.main()
