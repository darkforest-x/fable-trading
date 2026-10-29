"""Native hybrid admission, path ordering and idle-stream equivalence."""
import unittest
import numpy as np
import pandas as pd
from yoyo.evaluation.allin3_close_hybrid import configure_order
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native

CFG = dict(backstop_pct=30, be_offset_bp=17)


def native(path, skip, bars=None, rows=None):
    bars = bars or [[102]*4]*3 + [[102,104,96,98], [98,99,97,98],
        [98,112,97,111]] + [[111]*4]*7 + [[111,112,97,98],
        [98,105,97,103], [103,111,99,110]] + [[110]*4]*3
    frame = pd.DataFrame(bars, columns=['open','high','low','close'],
        index=pd.date_range('2026-01-06 00:05', periods=len(bars), freq='5min', tz='UTC'))
    raw = rows or [dict(trade_id='limit',entry_i=2,entry=100.,target_price=110.),
                   dict(trade_id='market',entry_i=13,entry=100.,target_price=110.)]
    entries = []
    for source in raw:
        row, status = configure_order(dict(side=1,expire_i=len(frame)-2,end_i=len(frame)-1,
            reclaim_minutes=60,**source),float(frame.close.iloc[source['entry_i']]),.1,CFG)
        assert status == 'admitted'; entries.append(row)
    policy = ExitPolicy(trail_arm_r=1e12,opposite_hour_exit=False,round_trip_cost=0,
        target_from_entry=True,intrabar_path=path,reclaim_next_bar=True,
        breakeven_includes_cost=False,fill_maker_fee=.0002,fill_taker_fee=.0005,
        monotone_segments=True,reclaim_backstop=True)
    return run_native(frame,np.full(len(frame),np.nan),np.zeros(len(frame)),pd.DataFrame(entries),
        policy,tick=.1,symbol='TEST',entry_order_mode='limit',batch_bars=2,skip_idle_bars=skip)


class HybridTests(unittest.TestCase):
    def test_long_and_short_mirror_and_equal_market(self):
        for side, target, favorable, rebound in [(1,110,98,102),(-1,90,102,98)]:
            row=dict(side=side,entry=100,target_price=target,entry_i=1,end_i=4,expire_i=3)
            for close in (favorable,100):
                r,status=configure_order(row,close,.1,CFG)
                self.assertEqual(status,'admitted'); self.assertEqual(r['order_mode'],'market')
                self.assertEqual(r['entry'],close); self.assertEqual(r['reclaim_ref'],100)
            r,_=configure_order(row,rebound,.1,CFG)
            self.assertEqual(r['order_mode'],'limit'); self.assertEqual(r['entry'],100)

    def test_already_at_target_and_beyond_stop_rejected(self):
        row=dict(side=1,entry=100,target_price=110,entry_i=1,end_i=4,expire_i=3)
        self.assertEqual(configure_order(row,110,.1,CFG)[1],'target_already_reached_at_decision_close')
        self.assertEqual(configure_order(row,69,.1,CFG)[1],'close_beyond_backstop')

    def test_target_first_cancels_but_limit_first_fills(self):
        bars=[[102]*4,[102,112,98,103],[103]*4,[103]*4]
        rows=[dict(trade_id='limit',entry_i=0,entry=100.,target_price=110.)]
        r,_,a=native('OHLC',True,bars,rows)
        self.assertTrue(r.empty); self.assertEqual(a['cancelled'],1)
        r,_,a=native('OLHC',True,bars,rows)
        self.assertEqual(len(r),1); self.assertEqual(r.iloc[0].reason,4)
        self.assertEqual(r.iloc[0].entry_i,1)

    def test_submission_does_not_fill_on_completed_wick(self):
        bars=[[102,103,90,102],[102,103,101,102],[102]*4,[102]*4]
        rows=[dict(trade_id='limit',entry_i=0,entry=100.,target_price=110.)]
        r,_,a=native('OHLC',True,bars,rows)
        self.assertTrue(r.empty); self.assertEqual(a['expired'],1)

    def test_idle_stream_exact_native_parity_both_paths(self):
        for path in ('OHLC','OLHC'):
            a,af,aa=native(path,False); b,bf,ba=native(path,True)
            pd.testing.assert_frame_equal(a,b)
            pd.testing.assert_frame_equal(af,bf)
            self.assertEqual(aa['opportunities'],ba['opportunities'])


if __name__=='__main__': unittest.main()
