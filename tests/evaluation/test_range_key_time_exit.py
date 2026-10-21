"""First-key half targets and timed exit contracts using native half positions."""
import unittest

import numpy as np
import pandas as pd

from yoyo.evaluation.range_key_time_exit import capped_legs
from yoyo.evaluation.range_v230_exit_compare import aggregate_legs
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy,run_native


def base(end=30):
    return pd.DataFrame([dict(trade_id='long',entry_i=2,entry=100.,stop=90.,risk=10.,side=1,end_i=end),
        dict(trade_id='short',entry_i=2,entry=100.,stop=110.,risk=10.,side=-1,end_i=end)])


class KeyTimeTests(unittest.TestCase):
    def test_nearest_key_half_and_ninety_minutes_from_actual_entry(self):
        capped,legs=capped_legs(base(),dict(long=[90,105,120],short=[80,95,110]),1.)
        self.assertEqual(list(capped.end_i),[20,20])
        self.assertEqual(list(legs.target_price[legs.leg==0]),[105,95])
        self.assertTrue(legs.loc[legs.leg==1,'target_price'].isna().all())
        self.assertTrue((legs.groupby('parent_id').weight.sum()==1).all())

    def test_missing_target_and_data_boundary(self):
        capped,legs=capped_legs(base(end=10),dict(long=[80,90],short=[110,120]),1.)
        self.assertTrue((capped.end_i==10).all())
        self.assertTrue(legs.target_price.isna().all())

    def test_cap_only_has_one_full_leg(self):
        _,legs=capped_legs(base(),dict(long=[105],short=[95]),1.,partial=False)
        self.assertEqual(len(legs),2)
        self.assertTrue((legs.weight==1).all())
        self.assertTrue(legs.target_price.isna().all())

    def test_native_partial_then_cap_and_fee_conservation(self):
        b=base().iloc[:1].copy()
        capped,legs=capped_legs(b,dict(long=[105,120]),1.)
        bars=[[100,101,99,100]]*3+[[100,106,99,105]]+[[105,108,102,107]]*18
        f=pd.DataFrame(bars,columns=['open','high','low','close'],index=pd.date_range('2026-01-01',periods=22,freq='5min',tz='UTC'))
        rows,fills,_=run_native(f,np.ones(len(f)),np.zeros(len(f)),legs,
            ExitPolicy(target_from_entry=True,opposite_hour_exit=False),tick=1.,symbol='TEST')
        self.assertEqual(sorted(rows.exit_i),[3,20])
        self.assertEqual(sorted(rows.reason),[0,4])
        p=aggregate_legs(rows,capped,.002).iloc[0]
        self.assertAlmostEqual(p.gross_r,.6)
        self.assertAlmostEqual(p.fee_r,.02)
        self.assertAlmostEqual(p.net_r,.58)
        self.assertEqual(len(fills),4)


if __name__=='__main__': unittest.main()
