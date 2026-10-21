"""Native-order contracts for causal 15m 2/2 pivot exits, including ties."""
import unittest
import importlib.util
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

if importlib.util.find_spec("nautilus_trader") is None:
    raise unittest.SkipTest("Run with the isolated Nautilus runtime")

from yoyo.layers.l3_backtest.nautilus_independent import (
    ExitPolicy, confirmed_pivots, run_native,
)


def fixture(quarters, entry_i=2, side=1, stop=98, **policy):
    bars = []
    for o, h, l, c in quarters:
        bars.extend([[o, o, o, o], [o, o, o, o], [o, h, l, c]])
    frame = pd.DataFrame(bars, columns=["open", "high", "low", "close"],
                         index=pd.date_range("2026-01-01 00:05", periods=len(bars),
                                             freq="5min", tz="UTC"))
    entry = frame.close.iloc[entry_i]
    events = pd.DataFrame([dict(trade_id="pivot", entry_i=entry_i, side=side,
                               entry=entry, stop=stop, risk=side*(entry-stop),
                               end_i=len(frame)-1)])
    rows, fills, audit = run_native(frame, np.ones(len(frame))*.1,
        np.zeros(len(frame)), events,
        ExitPolicy(confirmed_pivot_exit=True, opposite_hour_exit=False, **policy),
        tick=1., symbol="TEST")
    return frame, rows.iloc[0], fills, audit


BASE = [[100, 102, 99, 100], [100, 106, 99, 105], [105, 110, 104, 108],
        [108, 109, 103, 105], [105, 107, 102, 104], [104, 106, 101, 103]]


class PivotExitTests(unittest.TestCase):
    def test_confirmation_close_not_pivot_price_and_no_trailing(self):
        frame, row, fills, audit = fixture(BASE)
        self.assertEqual(row.reason, 7)
        self.assertEqual(row.exit_i, 14)
        self.assertEqual(row.exit_price, 104)
        self.assertEqual(row.pivot_price, 110)
        self.assertEqual(row.pivot_reference_r, 5)
        self.assertEqual(row.gross_r, 2)
        self.assertEqual(row.arm_i, -1)
        self.assertEqual(row.protection, 98)
        self.assertEqual(len(fills), 2)
        self.assertAlmostEqual(row.framework_commission, .002*row.entry*row.quantity)
        full = confirmed_pivots(frame)
        for end in range(3, len(frame)+1):
            prefix = confirmed_pivots(frame.iloc[:end])
            for actual, expected in zip(prefix, full):
                np.testing.assert_array_equal(actual, expected[:end])

    def test_short_mirror(self):
        bars = [[200-o, 200-l, 200-h, 200-c] for o,h,l,c in BASE]
        _, row, _, _ = fixture(bars, side=-1, stop=102)
        self.assertEqual(row.reason, 7)
        self.assertEqual(row.exit_i, 14)
        self.assertEqual(row.exit_price, 96)
        self.assertEqual(row.pivot_price, 90)

    def test_stop_on_confirmation_bar_wins(self):
        bars = [b.copy() for b in BASE]
        bars[4][2] = 97
        _, row, _, _ = fixture(bars)
        self.assertEqual(row.reason, 1)
        self.assertEqual(row.exit_i, 14)
        self.assertEqual(row.exit_price, 98)

    def test_pivot_center_before_entry_is_not_an_exit(self):
        _, row, _, _ = fixture(BASE, entry_i=8, stop=98)
        self.assertEqual(row.reason, 0)

    def test_profitable_extreme_can_confirm_at_a_loss(self):
        bars = [b.copy() for b in BASE]
        bars[4] = [105, 107, 98.5, 99]
        # Use integer tick-aligned OHLC while remaining above the stop.
        bars[4][2] = 99
        bars[5] = [99, 101, 99, 100]
        _, row, _, _ = fixture(bars)
        self.assertEqual(row.reason, 7)
        self.assertEqual(row.exit_price, 99)
        self.assertLess(row.net_r, 0)

    def test_equal_extreme_sensitivity(self):
        bars = [b.copy() for b in BASE]
        bars[1][1] = 110
        _, latest, _, _ = fixture(bars)
        _, strict, _, _ = fixture(bars, strict_pivot_ties=True)
        self.assertEqual(latest.reason, 7)
        self.assertEqual(strict.reason, 0)

    def test_policy_rejects_mixed_exit_rules(self):
        with self.assertRaises(ValueError):
            ExitPolicy(confirmed_pivot_exit=True)
        with self.assertRaises(ValueError):
            ExitPolicy(confirmed_pivot_exit=True, opposite_hour_exit=False, fixed_target_r=2)

    def test_framework_preserves_provided_wick_stop_and_projects_inputs(self):
        from yoyo.evaluation.range_framework import prepared
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frame = pd.DataFrame({"ts": pd.date_range("2026-01-01", periods=60,
                freq="5min", tz="UTC").as_unit("ns").asi8//1_000_000,
                "open":100.,"high":102.,"low":98.,"close":101.})
            frame.to_csv(root/"bars.csv",index=False)
            pd.DataFrame([dict(pair_id=0,entry_i=45,side=1,segment=0,week=0,bucket=0,
                end_i=59,entry_time="2026-01-01T03:50:00Z",population="signal",
                entry=101.,stop=90.,exit_price=999999)]).to_csv(root/"entries.csv",index=False)
            cfg=dict(data=str(root/"bars.csv"),entry_candidates=str(root/"entries.csv"),
                     entry_mode="provided",stop_window_5m=1,buffer_percent=0,tick=1)
            _, entries, hit = prepared(cfg,root/"cache")
            self.assertFalse(hit)
            self.assertEqual(entries.iloc[0].stop,90)
            self.assertEqual(entries.iloc[0].risk,11)
            self.assertNotIn("exit_price",entries)
            _, cached, hit = prepared(cfg,root/"cache")
            self.assertTrue(hit)
            pd.testing.assert_frame_equal(entries,cached)


if __name__ == "__main__":
    unittest.main()
