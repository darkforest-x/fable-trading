"""Pyramid legs (滚仓) on the reclaim rule: armed at the reclaim, stop entries in the trade's favour, ratcheted stops."""
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
    base = dict(entry_i=0, expire_i=max(1, len(frame)-2), end_i=len(frame)-1, side=1, stop=np.nan, target_price=130.,
                reclaim_minutes=1, pyramid_parent=None, pyramid_step=0, ratchet=np.nan)
    entries = pd.DataFrame([base | r for r in rows])
    entries['risk'] = entries.entry
    policy = ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0., target_from_entry=True,
                        intrabar_path='OHLC', fill_maker_fee=.0002, fill_taker_fee=.0005, reclaim_next_bar=True,
                        breakeven_includes_cost=False, monotone_segments=True)
    rows_, fills, audit = run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)), entries, policy, tick=.1,
                                     symbol='TEST', execution_minutes=1, entry_order_mode='limit')
    return (rows_.set_index('trade_id') if len(rows_) else pd.DataFrame(index=pd.Index([], name='trade_id'))), fills, audit


PARENT = dict(trade_id='p', entry=100.)
ADD1 = dict(trade_id='a1', entry=110., pyramid_parent='p', pyramid_step=1, ratchet=100.)
ADD2 = dict(trade_id='a2', entry=120., pyramid_parent='p', pyramid_step=2, ratchet=110.)
# bar 0 submits; bar 1 fills the parent at 100; bar 2 closes back inside (reclaim, breakeven at 100, pyramid armed)
START = [[102]*4, [102, 103, 99, 101], [101, 103, 100.5, 102]]


def statuses(audit, tid):
    return {o['status'] for o in audit['opportunities'] if o['trade_id'] == tid}


class PyramidTests(unittest.TestCase):
    def test_adds_fill_in_favour_and_the_ratchet_closes_all_legs_together(self):
        r, _, _ = replay(START+[[102, 125, 102, 124], [124, 124, 108, 109], [109]*4, [109]*4], [PARENT, ADD1, ADD2])
        self.assertEqual((r.loc['a1', 'entry'], r.loc['a2', 'entry']), (110., 120.))
        self.assertEqual({r.loc[k, 'exit_price'] for k in ('p', 'a1', 'a2')}, {110.})
        self.assertEqual({r.loc[k, 'reason'] for k in ('p', 'a1', 'a2')}, {13})
        self.assertGreater(r.loc['p', 'net_notional_bp'], 900)
        self.assertLess(r.loc['a2', 'net_notional_bp'], -800)

    def test_all_legs_exit_at_the_shared_target(self):
        r, _, _ = replay(START+[[102, 131, 102, 130.5], [130.5]*4, [130.5]*4], [PARENT, ADD1, ADD2])
        self.assertEqual({r.loc[k, 'reason'] for k in ('p', 'a1', 'a2')}, {4})
        self.assertEqual({r.loc[k, 'exit_price'] for k in ('p', 'a1', 'a2')}, {130.})

    def test_failed_reclaim_never_arms_the_legs(self):
        r, _, audit = replay([[102]*4, [102, 103, 99, 101], [101, 101, 95, 96], [96]*4, [96]*4], [PARENT, ADD1])
        self.assertEqual(r.loc['p', 'reason'], 11)
        self.assertNotIn('a1', r.index)
        self.assertEqual(statuses(audit, 'a1'), {'pyramid_not_armed'})

    def test_level_already_passed_at_the_decision_is_not_added_late(self):
        r, _, audit = replay([[102]*4, [102, 103, 99, 101], [101, 115, 100.5, 114], [114]*4, [114]*4], [PARENT, ADD1, ADD2])
        self.assertEqual(statuses(audit, 'a1'), {'pyramid_passed'})
        self.assertNotIn('a1', r.index)

    def test_pending_legs_are_cancelled_when_the_parent_stops_out(self):
        r, _, audit = replay(START+[[102, 104, 99, 99.5], [99.5]*4, [99.5]*4], [PARENT, ADD1])
        self.assertEqual((r.loc['p', 'reason'], r.loc['p', 'exit_price']), (5, 100.))
        self.assertEqual(statuses(audit, 'a1'), {'parent_closed'})

    def test_unfilled_parent_leaves_its_legs_unarmed(self):
        r, _, audit = replay([[102]*4, [102, 103, 101, 102], [102]*4, [102]*4], [PARENT, ADD1])
        self.assertEqual(statuses(audit, 'a1'), {'pyramid_not_armed'})
        self.assertNotIn('p', r.index)

    def test_invalid_legs_are_rejected(self):
        bars = START+[[102]*4, [102]*4]
        for bad in (ADD1 | dict(entry=99.), ADD1 | dict(ratchet=115.), ADD1 | dict(ratchet=95.),
                    ADD1 | dict(target_price=140.), ADD1 | dict(pyramid_parent='x')):
            with self.assertRaises(ValueError):
                replay(bars, [PARENT, bad])


if __name__ == '__main__':
    unittest.main()
