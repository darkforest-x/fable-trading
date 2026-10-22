"""Native fixed-target limits must respect entry timing and tranche isolation."""
import importlib.util
import unittest
import numpy as np
import pandas as pd

if importlib.util.find_spec('nautilus_trader') is None:
    raise unittest.SkipTest('Use the isolated Nautilus runtime')
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native
from yoyo.evaluation.monday_fib_limits import geometry, path_oracle


def replay(bars, side=1, targets=(104., 106.)):
    frame = pd.DataFrame(bars, columns=['open','high','low','close'],
        index=pd.date_range('2026-01-06', periods=len(bars), freq='min', tz='UTC'))
    entries = pd.DataFrame([dict(trade_id=f'leg-{j}', entry_i=0, entry=100.,
        side=side, stop=100.-side*2, risk=2., target_price=t,
        expire_i=len(bars)-2, end_i=len(bars)-1) for j,t in enumerate(targets)])
    return run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)),
        entries, ExitPolicy(target_from_entry=True, opposite_hour_exit=False,
                            round_trip_cost=0), tick=.1, symbol='TEST',
        execution_minutes=1, entry_order_mode='limit')


class FixedLimitTests(unittest.TestCase):
    def test_owner_image_prices(self):
        entry,stop,tp80,tp20=geometry(83149.8,81166.,-1,.1,.007)
        self.assertAlmostEqual(entry,84375.8)
        self.assertAlmostEqual(tp80,81923.8)
        self.assertAlmostEqual(tp20,81166.)
        self.assertGreaterEqual(stop,entry*1.007)

    def test_long_geometry_is_opposite_endpoint_mirror(self):
        entry,stop,tp80,tp20=geometry(110.,100.,1,.001,.007)
        self.assertAlmostEqual(entry,93.82)
        self.assertAlmostEqual(tp80,106.18)
        self.assertAlmostEqual(tp20,110.)
        self.assertLessEqual(stop,entry*.993)

    def test_pre_entry_high_cannot_fill_long_target(self):
        rows,_,_ = replay([[102]*4,[102,107,99,101],[101]*4,[101]*4])
        self.assertTrue(rows.reason.eq(0).all())

    def test_entry_stop_cancels_both_tranches(self):
        rows,fills,_ = replay([[102]*4,[102,107,97,101],[101]*4,[101]*4])
        self.assertTrue(rows.reason.eq(1).all())
        self.assertTrue(rows.exit_price.eq(98).all())
        self.assertEqual(len(fills),4)

    def test_short_target_after_entry_in_same_minute(self):
        rows,_,_ = replay([[98]*4,[98,101,93,95],[95]*4,[95]*4], -1, (96.,94.))
        self.assertTrue(rows.reason.eq(4).all())
        self.assertTrue(rows.exit_i.eq(1).all())

    def test_partial_then_unchanged_runner_stop(self):
        rows,_,_ = replay([[102]*4,[102,103,99,101],[101,105,100,103],
                          [103,103,97,99],[99]*4])
        rows=rows.set_index('trade_id')
        self.assertEqual(rows.loc['leg-0','reason'],4)
        self.assertEqual(rows.loc['leg-1','reason'],1)
        self.assertAlmostEqual(.8*rows.loc['leg-0','gross_r']+.2*rows.loc['leg-1','gross_r'],1.4)

    def test_true_gap_keeps_adverse_stop_fill(self):
        rows,_,_ = replay([[102]*4,[97,99,96,98],[98]*4,[98]*4])
        self.assertTrue(rows.exit_price.eq(97).all())

    def test_unfilled_limits_expire(self):
        rows,_,audit = replay([[102]*4,[102]*4,[102]*4,[99]*4])
        self.assertTrue(rows.empty)
        self.assertEqual(audit['expired'],2)

if __name__ == '__main__':
    unittest.main()
