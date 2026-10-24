"""Synthetic admission fixtures; no historical replay or custom fill simulator."""
import unittest
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_limit_reclaim import prepare


class ReclaimAdmissionTests(unittest.TestCase):
    def fixture(self):
        index=pd.date_range('2026-10-19T04:00Z',periods=318,freq='5min')
        f=pd.DataFrame(dict(open=100.,high=102.,low=98.,close=100.,volume=1.),index=index)
        f.iloc[288,f.columns.get_loc('low')]=95.
        f.attrs['group']='crypto_5m'
        job=dict(symbol='TESTUSDT',group='crypto_5m',execution_minutes=5,confirm_minutes=[5,15,60])
        return f,job,dict(seed=1,controls_per_signal=0)

    def test_first_touch_orders_preserve_five_targets_without_initial_risk_stop(self):
        f,job,cfg=self.fixture()
        _,orders,opps,_,_=prepare(f,job,cfg,{})
        self.assertEqual(len(orders),15)
        self.assertEqual(set(opps.status),{'admitted','no_observed_touch'})
        self.assertTrue(orders.stop.isna().all())
        self.assertTrue(orders.risk.eq(orders.entry).all())
        self.assertTrue(orders.entry_i.eq(287).all())
        self.assertTrue(orders.asset_group.eq('ALTCOINS').all())
        np.testing.assert_allclose(orders.target_price.iloc[:5],[98.,100.,100.472,102.,104.472])
        self.assertEqual(set(orders.reclaim_minutes),{5,15,60})

    def test_gold_conflicting_monday_blocks_whole_week(self):
        f,job,cfg=self.fixture();f.attrs['group']='spot_gold';f['quality_segment']=0
        job.update(symbol='XAUUSD',group='spot_gold')
        _,orders,opps,_,_=prepare(f,job,cfg,{'blocked_monday_weeks':['2026-10-19']})
        self.assertTrue(orders.empty)
        self.assertTrue(opps.status.eq('unobservable_week').all())
        self.assertTrue(opps.detail.str.contains('conflict').all())


if __name__=='__main__':unittest.main()
