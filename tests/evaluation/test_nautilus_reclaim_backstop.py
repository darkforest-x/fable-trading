"""Reclaim with a resting backstop: stop from the fill, moved to entry on reclaim, cancelled on a failed reclaim."""
import importlib.util
import unittest
import numpy as np
import pandas as pd

if importlib.util.find_spec('nautilus_trader') is None:
    raise unittest.SkipTest('Use the isolated Nautilus runtime')
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native


def replay(bars, *, stop, side=1, target=150., backstop=True, path='OHLC'):
    frame = pd.DataFrame(bars, columns=['open', 'high', 'low', 'close'],
                         index=pd.date_range('2026-01-06', periods=len(bars), freq='min', tz='UTC'))
    entries = pd.DataFrame([dict(trade_id='b', entry_i=0, expire_i=1, end_i=len(frame)-1, entry=100., side=side,
                                 stop=stop, risk=100., target_price=target,
                                 reclaim_minutes=1)])
    policy = ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0., target_from_entry=True,
                        intrabar_path=path, fill_maker_fee=.0002, fill_taker_fee=.0005, reclaim_next_bar=True,
                        breakeven_includes_cost=False, monotone_segments=True, reclaim_backstop=backstop)
    rows, fills, _ = run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)), entries, policy, tick=.1,
                                symbol='TEST', execution_minutes=1, entry_order_mode='limit')
    return rows.iloc[0], fills


class ReclaimBackstopTests(unittest.TestCase):
    def test_backstop_fills_inside_the_fill_bar(self):
        r, fills = replay([[102]*4, [102, 103, 80, 101], [101]*4, [101]*4], stop=90.)
        self.assertEqual((r.reason, r.exit_i, r.exit_price), (1, 1, 90.))
        self.assertEqual(list(fills.phase), ['entry', 'stop'])

    def test_backstop_fills_before_the_confirmation_close(self):
        r, _ = replay([[102]*4, [102, 103, 95, 101], [101, 102, 85, 99], [99]*4], stop=90.)
        self.assertEqual((r.reason, r.exit_i, r.exit_price), (1, 2, 90.))

    def test_reclaim_moves_the_backstop_to_entry(self):
        r, fills = replay([[102]*4, [102, 103, 95, 101], [101, 103, 95, 102], [102, 103, 99, 101], [101]*4], stop=90.)
        self.assertEqual((r.reason, r.exit_i, r.exit_price, r.reclaim_status), (5, 3, 100., 'reclaimed'))
        self.assertEqual(list(fills.phase), ['entry', 'stop'])

    def test_failed_reclaim_cancels_the_backstop(self):
        r, fills = replay([[102]*4, [102, 103, 95, 101], [101, 102, 92, 98], [98, 98, 70, 70], [70]*4], stop=90.)
        self.assertEqual((r.reason, r.exit_i, r.exit_price, r.reclaim_status), (11, 2, 98., 'failed'))
        self.assertEqual(len(fills), 2)

    def test_untouched_backstop_matches_the_rule_without_it(self):
        bars = [[102]*4, [102, 103, 30, 101], [101, 103, 20, 102], [102, 103, 99, 101], [101]*4]
        a, _ = replay(bars, stop=10.)
        b, _ = replay(bars, stop=np.nan, backstop=False)
        for c in ('entry_i', 'exit_i', 'exit_price', 'reason', 'reclaim_i', 'net_notional_bp'):
            self.assertEqual(a[c], b[c])

    def test_short_mirror(self):
        r, _ = replay([[98]*4, [98, 120, 97, 99], [99]*4, [99]*4], stop=110., side=-1, target=50.)
        self.assertEqual((r.reason, r.exit_i, r.exit_price), (1, 1, 110.))

    def test_backstop_needs_the_reclaim_rule_and_finite_stops(self):
        with self.assertRaises(ValueError):
            ExitPolicy(target_from_entry=True, opposite_hour_exit=False, reclaim_backstop=True)
        with self.assertRaises(ValueError):
            replay([[102]*4, [102, 103, 95, 101], [101]*4], stop=np.nan)
        with self.assertRaises(ValueError):
            replay([[102]*4, [102, 103, 95, 101], [101]*4], stop=105.)


if __name__ == '__main__':
    unittest.main()
