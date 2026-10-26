"""Reclaim breakeven offsets and scale-in legs that share their parent's exits."""
import importlib.util
import unittest
import numpy as np
import pandas as pd

if importlib.util.find_spec('nautilus_trader') is None:
    raise unittest.SkipTest('Use the isolated Nautilus runtime')
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native


def replay(bars, rows):
    frame = pd.DataFrame(bars, columns=['open', 'high', 'low', 'close'],
                         index=pd.date_range('2026-01-06', periods=len(bars), freq='min', tz='UTC'))
    base = dict(entry_i=0, expire_i=max(1, len(frame)-2), end_i=len(frame)-1, side=1, stop=np.nan, target_price=150., reclaim_minutes=1)
    entries = pd.DataFrame([base | r for r in rows])
    entries['risk'] = entries.entry
    policy = ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0., target_from_entry=True,
                        intrabar_path='OHLC', fill_maker_fee=.0002, fill_taker_fee=.0005, reclaim_next_bar=True,
                        breakeven_includes_cost=False, monotone_segments=True)
    rows_, fills, audit = run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)), entries, policy, tick=.1,
                                     symbol='TEST', execution_minutes=1, entry_order_mode='limit')
    return rows_.set_index('trade_id'), fills, audit


PARENT = dict(trade_id='p', entry=100.)
CHILD = dict(trade_id='c', entry=95., parent='p', reclaim_ref=100.)


class BreakevenOffsetTests(unittest.TestCase):
    def test_offset_moves_the_breakeven_stop_beyond_entry(self):
        r, _, _ = replay([[102]*4, [102, 103, 99, 101], [101, 103, 100.5, 102], [102, 102, 100.1, 100.5], [100.5]*4],
                         [PARENT | dict(be_offset_bp=7.)])
        self.assertEqual((r.loc['p', 'reason'], r.loc['p', 'exit_price']), (5, 100.1))

    def test_close_inside_the_fee_band_exits_at_the_close(self):
        r, _, _ = replay([[102]*4, [102, 103, 99, 101], [101, 101, 100, 100.1], [100.1]*4], [PARENT | dict(be_offset_bp=17.)])
        self.assertEqual((r.loc['p', 'reason'], r.loc['p', 'exit_price'], r.loc['p', 'reclaim_status']), (12, 100.1, 'reclaimed_inside_be'))

    def test_zero_offset_matches_the_plain_rule(self):
        bars = [[102]*4, [102, 103, 30, 101], [101, 103, 20, 102], [102, 103, 99, 101], [101]*4]
        a, _, _ = replay(bars, [PARENT | dict(be_offset_bp=0.)])
        b, _, _ = replay(bars, [PARENT])
        for c in ('exit_i', 'exit_price', 'reason', 'net_notional_bp'):
            self.assertEqual(a.loc['p', c], b.loc['p', c])


class ScaleInTests(unittest.TestCase):
    def test_both_legs_exit_at_the_parent_breakeven(self):
        r, _, _ = replay([[102]*4, [102, 103, 94, 101], [101, 103, 98, 101], [101, 101, 99, 99.5], [99.5]*4], [PARENT, CHILD])
        self.assertEqual((r.loc['p', 'reason'], r.loc['p', 'exit_price']), (5, 100.))
        self.assertEqual((r.loc['c', 'reason'], r.loc['c', 'exit_price']), (5, 100.))
        self.assertGreater(r.loc['c', 'net_notional_bp'], 500)

    def test_failed_reclaim_is_judged_on_the_parent_entry(self):
        r, _, _ = replay([[102]*4, [102, 103, 94, 101], [101, 101, 96, 97], [97]*4], [PARENT, CHILD])
        self.assertEqual({r.loc[k, 'reason'] for k in 'pc'}, {11})
        self.assertEqual({r.loc[k, 'exit_price'] for k in 'pc'}, {97.})

    def test_child_filled_later_shares_the_parent_confirmation_clock(self):
        r, _, _ = replay([[102]*4, [102, 103, 99, 101], [101, 101, 94, 101], [101, 101, 99, 99.5], [99.5]*4], [PARENT, CHILD])
        self.assertEqual(r.loc['p', 'reclaim_i'], r.loc['c', 'reclaim_i'])
        self.assertEqual(r.loc['c', 'entry_i'], 2)

    def test_unfilled_child_is_cancelled_when_the_parent_closes(self):
        # Parent fills at bar 1 and reaches its target inside bar 2, before the decision close.
        r, _, audit = replay([[102]*4, [102, 103, 99, 101], [101, 104, 100.5, 102], [102, 102, 90, 91], [91]*4, [91]*4],
                             [PARENT | dict(target_price=103.), CHILD | dict(target_price=103.)])
        self.assertEqual(r.loc['p', 'reason'], 4)
        self.assertNotIn('c', r.index)
        self.assertIn('parent_closed', {o['status'] for o in audit['opportunities'] if o['trade_id'] == 'c'})

    def test_child_cannot_fill_after_the_parent_decision(self):
        # Reclaimed at bar 2; bar 4 drops through the parent breakeven and the child price in one bar.
        r, _, audit = replay([[102]*4, [102, 103, 99, 101], [101, 103, 100.5, 102], [102]*4, [102, 102, 90, 91], [91]*4, [91]*4],
                             [PARENT, CHILD])
        self.assertEqual((r.loc['p', 'reason'], r.loc['p', 'exit_price']), (5, 100.))
        self.assertNotIn('c', r.index)
        self.assertTrue({o['status'] for o in audit['opportunities'] if o['trade_id'] == 'c'} & {'expired', 'parent_closed'})

    def test_invalid_children_are_rejected(self):
        with self.assertRaises(ValueError):
            replay([[102]*4, [102]*4, [102]*4], [PARENT, CHILD | dict(parent='x')])
        with self.assertRaises(ValueError):
            replay([[102]*4, [102]*4, [102]*4], [PARENT, CHILD | dict(entry=101.)])


if __name__ == '__main__':
    unittest.main()
