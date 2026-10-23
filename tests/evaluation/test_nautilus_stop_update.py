"""A one-time stop tightening applies only from the close where it is first known."""
import importlib.util
import unittest
import numpy as np
import pandas as pd

if importlib.util.find_spec('nautilus_trader') is None:
    raise unittest.SkipTest('Use the isolated Nautilus runtime')
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native


def replay(bars, side=1, update_i=1, update=99.):
    frame = pd.DataFrame(bars, columns=['open', 'high', 'low', 'close'],
        index=pd.date_range('2026-01-06', periods=len(bars), freq='min', tz='UTC'))
    far = 100.-side*50
    entries = pd.DataFrame([dict(trade_id='t', entry_i=0, entry=100., side=side, stop=far, risk=50.,
        target_price=np.nan, expire_i=2, end_i=len(bars)-1, stop_update_i=update_i, stop_update=update)])
    return run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)), entries,
        ExitPolicy(trail_arm_r=1e12, target_from_entry=True, opposite_hour_exit=False, round_trip_cost=0),
        tick=.1, symbol='TEST', execution_minutes=1, entry_order_mode='limit')


class StopUpdateTests(unittest.TestCase):
    def test_wick_stop_hits_after_its_bar_closes(self):
        rows, _, _ = replay([[102]*4, [101, 101, 99, 100.5], [100.5, 101, 98.5, 99], [99]*4])
        self.assertEqual(int(rows.reason.iloc[0]), 9)
        self.assertAlmostEqual(rows.exit_price.iloc[0], 99.)
        self.assertEqual(int(rows.exit_i.iloc[0]), 2)

    def test_wick_bar_itself_cannot_stop(self):
        rows, _, _ = replay([[102]*4, [101, 101, 99, 100.5], [100.5]*4, [100.5]*4])
        self.assertEqual(int(rows.reason.iloc[0]), 0)
        self.assertAlmostEqual(rows.exit_price.iloc[0], 100.5)

    def test_short_mirror(self):
        rows, _, _ = replay([[98]*4, [99, 101, 99, 99.5], [99.5, 101.5, 99, 101], [101]*4], side=-1, update=101.)
        self.assertEqual(int(rows.reason.iloc[0]), 9)
        self.assertAlmostEqual(rows.exit_price.iloc[0], 101.)

    def test_close_at_wick_extreme_exits_at_that_close(self):
        rows, _, _ = replay([[98]*4, [99, 101, 99, 101], [100.5]*4, [100.5]*4], side=-1, update=101.)
        self.assertEqual(int(rows.reason.iloc[0]), 9)
        self.assertEqual(int(rows.exit_i.iloc[0]), 1)
        self.assertAlmostEqual(rows.exit_price.iloc[0], 101.)

    def test_no_update_keeps_terminal_exit(self):
        rows, _, _ = replay([[102]*4, [101, 101, 99, 100.5], [100.5, 101, 98.5, 99], [99]*4], update_i=np.nan)
        self.assertEqual(int(rows.reason.iloc[0]), 0)


if __name__ == '__main__':
    unittest.main()
