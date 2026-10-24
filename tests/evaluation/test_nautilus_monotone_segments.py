"""Synthetic intrabar segments must replay monotonically when monotone_segments is set."""
import importlib.util
import unittest
import numpy as np
import pandas as pd

if importlib.util.find_spec('nautilus_trader') is None:
    raise unittest.SkipTest('Use the isolated Nautilus runtime')
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native


def replay(side, entry, target, fill_bar, path, mono):
    flat = [fill_bar[0]]*4
    bars = [flat, fill_bar, [fill_bar[3]]*4, [fill_bar[3]]*4, [fill_bar[3]]*4]
    frame = pd.DataFrame(bars, columns=['open', 'high', 'low', 'close'],
                         index=pd.date_range('2026-01-06', periods=5, freq='5min', tz='UTC'))
    e = pd.DataFrame([dict(trade_id='t', entry_i=0, expire_i=3, end_i=4, side=side, entry=entry, stop=np.nan,
                           risk=entry, target_price=target, reclaim_minutes=60)])
    pol = ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0, target_from_entry=True,
                     intrabar_path=path, reclaim_next_bar=True, breakeven_includes_cost=False,
                     fill_maker_fee=0., fill_taker_fee=0., monotone_segments=mono)
    rows, _, _ = run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)), e, pol, tick=1., symbol='TST',
                            execution_minutes=5, entry_order_mode='limit')
    return int(rows.reason.iloc[0])


class MonotoneSegmentTests(unittest.TestCase):
    SHORT = (-1, 101., 99., [100, 102, 98, 101])
    LONG = (1, 99., 101., [100, 102, 98, 99])

    def test_short_low_first_cannot_reuse_the_low_after_the_fill(self):
        self.assertEqual(replay(*self.SHORT, 'OLHC', True), 0)
        self.assertEqual(replay(*self.SHORT, 'OHLC', True), 4)

    def test_long_high_first_cannot_reuse_the_high_after_the_fill(self):
        self.assertEqual(replay(*self.LONG, 'OHLC', True), 0)
        self.assertEqual(replay(*self.LONG, 'OLHC', True), 4)

    def test_default_keeps_the_previous_segment_order(self):
        self.assertEqual(replay(*self.SHORT, 'OLHC', False), 4)

    def test_monotone_requires_a_path(self):
        with self.assertRaises(ValueError):
            ExitPolicy(target_from_entry=True, opposite_hour_exit=False, monotone_segments=True)


if __name__ == '__main__':
    unittest.main()
