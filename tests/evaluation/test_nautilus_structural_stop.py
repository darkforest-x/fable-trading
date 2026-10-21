"""Causal native-order tests for opposite-pivot protection, not pivot-price exits."""
import importlib.util
import unittest

import numpy as np
import pandas as pd

if importlib.util.find_spec("nautilus_trader") is None:
    raise unittest.SkipTest("Use the isolated Nautilus runtime")

from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, confirmed_pivots, run_native


BARS = [[100,101,99,100], [104,106,103,105], [104,106,104,105],
        [104,109,101,107], [107,110,103,109], [109,112,108,111],
        [111,114,110,113], [111,112,106,110], [110,113,108,111],
        [111,115,110,114], [110,111,105,108]]


def replay(bars=None, side=1, entry_i=0, stop=90, **kwargs):
    frame = pd.DataFrame(BARS if bars is None else bars,
        columns=["open","high","low","close"],
        index=pd.date_range("2026-01-01 00:05", periods=len(BARS if bars is None else bars), freq="5min", tz="UTC"))
    if side == -1:
        frame = pd.DataFrame(dict(open=200-frame.open, high=200-frame.low,
            low=200-frame.high, close=200-frame.close),index=frame.index)
        stop = 200-stop
    entry = float(frame.close.iloc[entry_i])
    events = pd.DataFrame([dict(trade_id="structural",entry_i=entry_i,side=side,
        entry=entry,stop=stop,risk=side*(entry-stop),end_i=len(frame)-1)])
    rows,fills,audit=run_native(frame,np.ones(len(frame))*.1,np.zeros(len(frame)),events,
        ExitPolicy(structural_pivot_stop=True,pivot_minutes=5,opposite_hour_exit=False,**kwargs),
        tick=1.,symbol="TEST")
    return frame,rows.iloc[0],fills,audit


class StructuralStopTests(unittest.TestCase):
    def test_confirmed_stop_ratchets_and_fills_later(self):
        frame,row,fills,audit=replay()
        self.assertEqual(row.reason,8)
        self.assertEqual(row.exit_i,10)
        self.assertEqual(row.exit_price,106)
        self.assertEqual(row.structural_updates,2)
        self.assertEqual(row.arm_i,5)
        self.assertEqual(row.stop_kind,3)
        self.assertEqual([(u['bar_i'],u['stop']) for u in audit['protection_updates']],[(5,101),(9,106)])
        self.assertEqual(len(fills),2)
        self.assertAlmostEqual(row.net_r,.6-.02)
        self.assertAlmostEqual(row.framework_commission,row.entry*row.quantity*.002)
        self.assertEqual(audit['protection_updates'][0]['pivot_open_ns'],frame.index[2].value)

    def test_short_mirror(self):
        _,row,_,audit=replay(side=-1)
        self.assertEqual(row.reason,8)
        self.assertEqual(row.exit_price,94)
        self.assertEqual([u['stop'] for u in audit['protection_updates']],[99,94])

    def test_no_premature_fill_on_pivot_or_confirmation(self):
        _,row,_,audit=replay(BARS[:6])
        self.assertEqual(row.reason,0)
        self.assertEqual(row.exit_price,111)
        self.assertEqual(audit['protection_updates'],[])  # No redundant boundary amendment.
        _,row,_,audit=replay(BARS[:7])
        self.assertEqual(row.reason,0)
        self.assertEqual(row.exit_price,113)
        self.assertEqual(audit['protection_updates'][0]['bar_i'],5)

    def test_initial_stop_precedes_management(self):
        bars=[b.copy() for b in BARS]
        bars[5][2]=89
        _,row,_,audit=replay(bars)
        self.assertEqual((row.reason,row.exit_i,row.exit_price),(1,5,90))
        self.assertEqual(audit['protection_updates'],[])

    def test_same_entry_candle_center_is_excluded(self):
        _,row,_,audit=replay(BARS[:7],entry_i=3)
        self.assertEqual(row.reason,0)
        self.assertEqual(audit['protection_updates'],[])

    def test_gap_uses_actual_open_not_trailing_price(self):
        bars=[b.copy() for b in BARS]
        bars[10]=[103,104,101,102]
        frame,row,_,_=replay(bars)
        self.assertEqual(row.exit_price,103)
        self.assertEqual(pd.Timestamp(row.exit_time),frame.index[9])

    def test_protection_may_reduce_loss_before_breakeven(self):
        bars=[[110,111,109,110]]+[b.copy() for b in BARS[1:7]]
        _,row,_,audit=replay(bars)
        self.assertEqual(audit['protection_updates'][0]['stop'],101)
        self.assertLess(row.protection,row.entry)

    def test_strict_ties_are_explicit_sensitivity(self):
        bars=[b.copy() for b in BARS[:7]]
        bars[1][2]=101
        _,latest,_,_=replay(bars)
        _,strict,_,_=replay(bars,strict_pivot_ties=True)
        self.assertEqual(latest.structural_updates,1)
        self.assertEqual(strict.structural_updates,0)

    def test_pivot_prefix_invariance(self):
        frame,_,_,_=replay()
        full=confirmed_pivots(frame,pivot_minutes=5)
        for end in range(3,len(frame)+1):
            prefix=confirmed_pivots(frame.iloc[:end],pivot_minutes=5)
            for actual,expected in zip(prefix,full):
                np.testing.assert_array_equal(actual,expected[:end])

    def test_conflicting_policies_are_rejected(self):
        for extra in [dict(confirmed_pivot_exit=True),dict(fixed_target_r=2),
                      dict(breakeven_r=1),dict(target_from_entry=True),dict(opposite_hour_exit=True)]:
            with self.assertRaises(ValueError):
                ExitPolicy(**(dict(structural_pivot_stop=True,opposite_hour_exit=False)|extra))


if __name__=='__main__': unittest.main()
