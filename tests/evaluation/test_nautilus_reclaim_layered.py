"""Layered reclaim entries: fill-close exit on a resting limit, and completed-close market entries on the reclaim rule."""
import importlib.util
import unittest
import numpy as np
import pandas as pd

if importlib.util.find_spec('nautilus_trader') is None:
    raise unittest.SkipTest('Use the isolated Nautilus runtime')
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native


def replay(bars, *, mode='limit', entry_i=0, entry=100., stop=90., target=150., path='OHLC', **extra):
    frame = pd.DataFrame(bars, columns=['open', 'high', 'low', 'close'],
                         index=pd.date_range('2026-01-06', periods=len(bars), freq='min', tz='UTC'))
    entries = pd.DataFrame([dict(trade_id='x', entry_i=entry_i, expire_i=entry_i+1, end_i=len(frame)-1, entry=entry,
                                 side=1, stop=stop, risk=entry, target_price=target, reclaim_minutes=1, **extra)])
    policy = ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0., target_from_entry=True,
                        intrabar_path=path, fill_maker_fee=.0002, fill_taker_fee=.0005, reclaim_next_bar=True,
                        breakeven_includes_cost=False, monotone_segments=True, reclaim_backstop=True)
    rows, fills, _ = run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)), entries, policy, tick=.1,
                                symbol='TEST', execution_minutes=1, entry_order_mode=mode)
    return rows.iloc[0], fills


FILL = [[102]*4, [102, 103, 95, 101], [101, 103, 95, 102], [102, 103, 99, 101], [101]*4]


class FillCloseExitTests(unittest.TestCase):
    def test_flagged_fill_exits_at_its_own_close(self):
        r, fills = replay(FILL, fill_close_exit=True)
        self.assertEqual((r.reason, r.entry_i, r.exit_i, r.exit_price), (14, 1, 1, 101.))
        self.assertEqual(list(fills.phase), ['entry', '14'])

    def test_same_bar_target_wins_over_the_flag(self):
        r, _ = replay(FILL, fill_close_exit=True, target=102.5, path='OLHC')
        self.assertEqual((r.reason, r.exit_i, r.exit_price), (4, 1, 102.5))

    def test_same_bar_backstop_wins_over_the_flag(self):
        r, _ = replay([[102]*4, [102, 103, 80, 101], [101]*4, [101]*4], fill_close_exit=True)
        self.assertEqual((r.reason, r.exit_i, r.exit_price), (1, 1, 90.))

    def test_unflagged_fill_matches_no_column(self):
        a, _ = replay(FILL, fill_close_exit=False)
        b, _ = replay(FILL)
        for c in ('exit_i', 'exit_price', 'reason', 'reclaim_status', 'net_notional_bp'):
            self.assertEqual(a[c], b[c])

    def test_flag_needs_booleans_on_resting_entries(self):
        with self.assertRaises(ValueError):
            replay(FILL, fill_close_exit=1)
        with self.assertRaises(ValueError):
            replay(FILL, mode='market', entry_i=1, entry=101., fill_close_exit=True)


class CloseMarketReclaimTests(unittest.TestCase):
    def test_entry_below_the_level_reclaims_to_the_level(self):
        bars = [[102]*4, [102, 103, 95, 98], [98, 103, 97, 102], [102, 103, 99, 101], [101]*4]
        r, fills = replay(bars, mode='market', entry_i=1, entry=98., reclaim_ref=100.)
        self.assertEqual((r.reason, r.entry_i, r.exit_i, r.exit_price, r.reclaim_status), (5, 1, 3, 100., 'reclaimed'))
        self.assertEqual(list(fills.phase), ['entry', 'stop'])
        self.assertGreater(r.net_notional_bp, 0)

    def test_close_below_the_level_at_the_decision_fails(self):
        bars = [[102]*4, [102, 103, 95, 98], [98, 103, 97, 99], [99]*4, [99]*4]
        r, _ = replay(bars, mode='market', entry_i=1, entry=98., reclaim_ref=100.)
        self.assertEqual((r.reason, r.exit_i, r.exit_price, r.reclaim_status), (11, 2, 99., 'failed'))

    def test_backstop_rests_from_the_next_bar(self):
        bars = [[102]*4, [102, 103, 95, 98], [98, 99, 85, 97], [97]*4, [97]*4]
        r, _ = replay(bars, mode='market', entry_i=1, entry=98., reclaim_ref=100.)
        self.assertEqual((r.reason, r.exit_i, r.exit_price), (1, 2, 90.))

    def test_market_entry_must_be_the_completed_close(self):
        with self.assertRaises(ValueError):
            replay([[102]*4, [102, 103, 95, 98], [98]*4], mode='market', entry_i=1, entry=100., reclaim_ref=100.)


if __name__ == '__main__':
    unittest.main()
