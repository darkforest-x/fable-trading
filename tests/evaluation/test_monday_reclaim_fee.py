"""Native equivalence of inactive-interval removal for a tighter-stop study."""
import unittest
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_reclaim_fee import replay_envelope, restore_indices
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native
from yoyo.layers.l3_backtest.nautilus_reclaim_fee import run_native_fee_reclaim


class ReplayEnvelopeTests(unittest.TestCase):
    def test_tighter_stop_full_and_compressed_native_fills_are_identical(self):
        block=[[102]*4,[102,103,99,101],[101,103,99,102],
               [102,103,100.05,101],[101,102,99,101]]+[[102]*4]*5
        frame=pd.DataFrame(block*2,columns=['open','high','low','close'],
            index=pd.date_range('2026-01-06',periods=20,freq='min',tz='UTC'))
        entries=pd.DataFrame([dict(trade_id=f't{n}',entry_i=n,expire_i=n+1,end_i=n+8,
            entry=100.,side=1,stop=np.nan,risk=100.,target_price=110.,reclaim_minutes=1,
            submission_i=0,touch_i_hint=n+1) for n in [0,10]])
        policy=ExitPolicy(trail_arm_r=1e12,opposite_hour_exit=False,round_trip_cost=0,
            target_from_entry=True,intrabar_path='OHLC',reclaim_next_bar=True,
            breakeven_includes_cost=False,fill_maker_fee=.0002,fill_taker_fee=.0005)
        def run(fn,f,e):
            return fn(f,np.full(len(f),np.nan),np.zeros(len(f)),e,policy,tick=.01,
                      symbol='TEST',execution_minutes=1,entry_order_mode='limit',batch_bars=3)
        old,_,_=run(run_native,frame,entries)
        full,fullfills,_=run(run_native_fee_reclaim,frame,entries)
        compact,e,ix=replay_envelope(frame,entries,old)
        self.assertEqual(len(compact),10)
        short,shortfills,_=run(run_native_fee_reclaim,compact,e)
        short,shortfills=restore_indices(short,shortfills,ix)
        for column in ['trade_id','entry_i','exit_i','entry','exit_price','reason','fee_r',
                       'net_notional_bp','reclaim_i','be_effective_i','fee_be_effective_i']:
            pd.testing.assert_series_equal(full[column],short[column],check_dtype=False)
        columns=['trade_id','phase','bar_i','ts_ns','price','quantity','commission']
        pd.testing.assert_frame_equal(fullfills[columns],shortfills[columns])
        self.assertTrue((short.exit_i<old.exit_i).all())

    def test_refuses_missing_affected_order(self):
        frame=pd.DataFrame(index=pd.date_range('2026-01-01',periods=4,freq='min',tz='UTC'))
        with self.assertRaises(ValueError):
            replay_envelope(frame,pd.DataFrame([dict(trade_id='a',entry_i=0)]),
                            pd.DataFrame([dict(trade_id='b',exit_i=3)]))


if __name__=='__main__':unittest.main()
