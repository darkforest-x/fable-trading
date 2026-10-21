"""Real native limit-entry timing, expiration, gaps and protective-stop tests."""
import importlib.util
import unittest

import numpy as np
import pandas as pd

if importlib.util.find_spec("nautilus_trader") is None:
    raise unittest.SkipTest("Use the existing isolated Nautilus environment")

from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native


def replay(bars, side=1, limit=100., stop=98., expires=2, count=1):
    frame = pd.DataFrame(bars, columns=["open", "high", "low", "close"],
        index=pd.date_range("2026-01-06 00:05", periods=len(bars), freq="5min", tz="UTC"))
    entries = pd.DataFrame([dict(trade_id=f"limit-{j}", entry_i=0, side=side,
        entry=limit, stop=stop, risk=side*(limit-stop), end_i=len(bars)-1,
        expire_i=expires) for j in range(count)])
    return run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)),
        entries, ExitPolicy(confirmed_pivot_exit=True, opposite_hour_exit=False),
        tick=.1, symbol="TEST", entry_order_mode="limit")


class LimitEntryTests(unittest.TestCase):
    def test_no_retrospective_fill_and_expiration(self):
        rows, fills, audit = replay([[102,110,90,102],[102,103,101,102],
            [102,103,101,102],[102,103,99,102]])
        self.assertTrue(rows.empty)
        self.assertTrue(fills.empty)
        self.assertEqual(audit['expired'], 1)

    def test_expiry_bar_included_and_next_bar_excluded(self):
        rows, fills, audit = replay([[102]*4,[102,103,101,102],
            [102,103,100,102],[102,103,101,102]])
        self.assertEqual(rows.iloc[0].entry_i, 2)
        self.assertEqual(rows.iloc[0].entry, 100.)
        self.assertEqual(audit['expired'], 0)
        self.assertEqual(len(fills), 2)

    def test_entry_and_stop_same_bar_count_as_loss(self):
        rows, fills, _ = replay([[102]*4,[102,104,97,101],[101]*4,[101]*4])
        row = rows.iloc[0]
        self.assertEqual(row.entry_i, 1)
        self.assertEqual(row.exit_i, 1)
        self.assertEqual(row.exit_price, 98.)
        self.assertEqual(row.peak_r, 0)
        self.assertEqual(row.reason, 1)
        self.assertEqual(len(fills), 2)

    def test_gap_beyond_stop_is_not_cancelled_or_forgiven(self):
        rows, _, _ = replay([[102]*4,[97,101,96,99],[99]*4,[99]*4])
        row=rows.iloc[0]
        self.assertEqual(row.entry, 100.)
        self.assertEqual(row.exit_i, 1)
        self.assertEqual(row.exit_price, 97.)
        self.assertAlmostEqual(row.framework_commission, .002*100*row.quantity)

    def test_simultaneous_orders_have_independent_fills(self):
        rows, fills, audit = replay([[102]*4,[102,103,100,102],
            [102,103,101,102],[102]*4], count=3)
        self.assertEqual(len(rows), 3)
        self.assertTrue(rows.entry_i.eq(1).all())
        self.assertEqual(len(fills), 6)
        self.assertEqual(audit['positions'], 3)

    def test_short_mirror_and_stop(self):
        rows, _, _ = replay([[98]*4,[98,103,97,99],[99]*4,[99]*4],
            side=-1, limit=100, stop=102)
        self.assertEqual(rows.iloc[0].exit_price, 102.)
        self.assertEqual(rows.iloc[0].reason, 1)

    def test_entry_bar_favorable_high_not_counted(self):
        rows, _, _ = replay([[102]*4,[102,110,99,101],[101]*4,[101]*4])
        self.assertEqual(rows.iloc[0].peak_r, .5)

    def test_later_gap_does_not_reuse_entry_bar_stop_price(self):
        rows, _, _ = replay([[102]*4,[102,103,99,101],[95,99,94,97],[97]*4])
        self.assertEqual(rows.iloc[0].exit_i, 2)
        self.assertEqual(rows.iloc[0].exit_price, 95.)


if __name__ == '__main__':
    unittest.main()
