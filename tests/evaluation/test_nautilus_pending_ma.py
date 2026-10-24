"""Native causal pending-limit amendments and price-boundary cancellation."""
import unittest
import numpy as np
import pandas as pd
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native


def replay(bars, updates=(), side=1, entry=100., stop=98., boundary=108., target=112., tick=.1):
    f = pd.DataFrame(bars, columns=['open','high','low','close'],
        index=pd.date_range('2026-01-06 00:05', periods=len(bars), freq='5min', tz='UTC'))
    e = pd.DataFrame([dict(trade_id='x', entry_i=0, side=side, entry=entry,
        stop=stop, risk=side*(entry-stop), target_price=target,
        cancel_boundary=boundary, expire_i=len(f)-2, end_i=len(f)-1)])
    return run_native(f, np.full(len(f),np.nan),np.zeros(len(f)),e,
        ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0,
                   target_from_entry=True, intrabar_path='OHLC',
                   fill_maker_fee=.0002,fill_taker_fee=.0005),
        tick=tick, symbol='TEST',entry_order_mode='limit',
        entry_updates=pd.DataFrame(updates) if updates else None)


class PendingMaTests(unittest.TestCase):
    def test_amendment_not_retroactive_and_stop_frozen(self):
        r,f,a=replay([[104]*4,[104,105,101,104],[104,105,101,103],[103,113,102,112],[112]*4],
          [dict(trade_id='x',bar_i=1,entry=102.,stop=99.,action='reprice'),
           dict(trade_id='x',bar_i=2,entry=103.,stop=100.,action='reprice')])
        self.assertEqual(r.iloc[0].entry_i,2)
        self.assertEqual(r.iloc[0].entry,102.)
        self.assertEqual(r.iloc[0].stop,99.)
        self.assertEqual(r.iloc[0].exit_price,112.)
        self.assertEqual(len(a['pending_amendments']),1)
        self.assertAlmostEqual(r.iloc[0].fee_r,(102+112)*.0002/3)

    def test_range_touched_before_retest_cancels(self):
        r,f,a=replay([[104]*4,[104,109,99,103],[103]*4,[103]*4])
        self.assertTrue(r.empty)
        self.assertTrue(f.empty)
        self.assertEqual(a['opportunities'][0]['status'],'returned_to_monday_range')

    def test_fill_before_later_range_touch_survives(self):
        r,f,a=replay([[104]*4,[99,113,99,112],[112]*4,[112]*4])
        self.assertEqual(r.iloc[0].entry,100.)
        self.assertEqual(r.iloc[0].exit_price,112.)
        self.assertEqual(a['cancelled'],0)

    def test_third_low_cancel_after_native_fill_does_not_erase_loss(self):
        r,f,a=replay([[104]*4,[104,105,97,99],[99]*4,[99]*4],
          [dict(trade_id='x',bar_i=1,action='cancel',status='third_new_extreme')])
        self.assertEqual(r.iloc[0].exit_price,98.)
        self.assertEqual(r.iloc[0].reason,1)

    def test_short_range_boundary_after_high_allows_fill(self):
        r,f,a=replay([[96]*4,[96,101,87,90],[90]*4,[90]*4],
                    side=-1,stop=102.,boundary=92.,target=88.)
        self.assertEqual(r.iloc[0].entry,100.)
        self.assertEqual(r.iloc[0].exit_price,88.)

    def test_subtick_fixed_point_float_rounding_at_large_notional(self):
        r,f,a=replay([[3100]*4,[3100,3101,3080,3090],[3090,3141,3085,3140],[3140]*4],
                    entry=3082.631858,stop=3070.,boundary=3130.,target=3140.,tick=.000001)
        self.assertEqual(len(r),1)
        self.assertLess(abs(f.iloc[0].price-r.iloc[0].entry),.000001/1000000)
        self.assertEqual(r.iloc[0].reason,4)

    def test_marketable_amendment_cancelled(self):
        r,f,a=replay([[104]*4,[104,105,101,102],[102]*4,[102]*4],
          [dict(trade_id='x',bar_i=1,entry=103.,stop=99.,action='reprice')])
        self.assertTrue(r.empty)
        self.assertEqual(a['opportunities'][0]['status'],'amendment_not_resting')


if __name__=='__main__': unittest.main()
