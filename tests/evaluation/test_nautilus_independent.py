"""Behavioral contract tests using real Nautilus orders, not a replay mock.

Run in the existing isolated Nautilus environment with unittest. Synthetic bars
cover close-entry timing, gaps, simultaneous hedges, next-bar protection,
segment boundaries and a fee-aware breakeven trigger independent of trailing.
"""
import unittest
import importlib.util

import numpy as np
import pandas as pd

if importlib.util.find_spec("nautilus_trader") is None:
    raise unittest.SkipTest("Run with the isolated Nautilus runtime, not the model-training venv")

from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native


def replay(bars, events, atr=None, hour=None, tick=1., market_tick=None, **policy):
    frame = pd.DataFrame(
        bars, columns=["open", "high", "low", "close"],
        index=pd.date_range("2026-01-01", periods=len(bars), freq="5min", tz="UTC"),
    )
    rows = []
    for j, (first, side, stop, last) in enumerate(events):
        entry = bars[first][3]
        rows.append(dict(trade_id=f"test-{j}", entry_i=first, side=side,
                         entry=entry, stop=stop, risk=side*(entry-stop), end_i=last))
    return run_native(
        frame, np.full(len(bars), np.nan) if atr is None else atr,
        np.zeros(len(bars)) if hour is None else hour, pd.DataFrame(rows),
        ExitPolicy(**policy), tick=tick, symbol="TEST", market_tick=market_tick,
    )


class NativeContractTests(unittest.TestCase):
    def test_independent_hedges_entry_bar_excluded_and_gap(self):
        rows, fills, audit = replay(
            [[100, 150, 50, 100], [100, 101, 99, 100], [97, 100, 96, 99]],
            [(0, 1, 98, 2), (0, -1, 103, 2)],
        )
        result = rows.set_index("trade_id")
        self.assertEqual(result.loc["test-0", "exit_price"], 97)
        self.assertEqual(result.loc["test-0", "reason"], 1)
        self.assertEqual(result.loc["test-0", "peak_r"], .5)
        self.assertEqual(result.loc["test-1", "reason"], 0)
        self.assertEqual(audit["positions"], 2)
        self.assertEqual(len(fills), 4)
        self.assertGreater(audit["bars_with_both_directions"], 0)

    def test_close_protection_effective_next_bar_and_fee_model(self):
        rows, fills, _ = replay(
            [[100, 100, 100, 100], [100, 104, 100, 103], [103, 104, 100, 101]],
            [(0, 1, 99, 2)], atr=[np.nan, .25, np.nan], breakeven_r=3.,
        )
        row = rows.iloc[0]
        self.assertEqual(row.be_effective_i, 1)
        self.assertEqual(row.exit_i, 2)
        self.assertEqual(row.reason, 5)
        self.assertEqual(row.exit_price, 101)
        self.assertAlmostEqual(row.framework_commission, .002*row.entry*row.quantity)
        self.assertEqual(len(fills), 2)

    def test_be_alone_does_not_enable_reverse_exit(self):
        rows, _, _ = replay(
            [[100, 100, 100, 100], [100, 104, 100, 103], [103, 104, 102, 103]],
            [(0, 1, 99, 2)], atr=[np.nan, .25, .25],
            hour=[0, -1, -1], breakeven_r=3.,
        )
        self.assertEqual(rows.iloc[0].reason, 0)
        self.assertEqual(rows.iloc[0].arm_i, -1)

    def test_new_trailing_and_reverse_close_same_bar(self):
        rows, _, _ = replay(
            [[100, 100, 100, 100], [100, 106, 100, 105], [105, 105, 104, 105]],
            [(0, 1, 99, 2)], atr=[np.nan, .25, .25], hour=[0, -1, 0],
        )
        self.assertEqual(rows.iloc[0].reason, 3)
        self.assertEqual(rows.iloc[0].exit_price, 105)
        self.assertEqual(rows.iloc[0].exit_i, 1)

    def test_initial_stop_precedes_close_recovery(self):
        rows, _, _ = replay(
            [[100, 100, 100, 100], [100, 110, 98, 106], [106, 106, 105, 106]],
            [(0, 1, 99, 2)], atr=[np.nan, .25, .25], hour=[0, -1, 0],
        )
        self.assertEqual(rows.iloc[0].reason, 1)
        self.assertEqual(rows.iloc[0].peak_r, 0)
        self.assertEqual(rows.iloc[0].arm_i, -1)

    def test_cost_be_deferred_then_gap_can_lose(self):
        rows, _, _ = replay(
            [[10000]*4, [10000, 10004, 10000, 10003],
             [10003, 10023, 10002, 10022], [10015, 10020, 10010, 10018]],
            [(0, 1, 9999, 3)], atr=[np.nan, 100, 100, np.nan], breakeven_r=3.,
        )
        row = rows.iloc[0]
        self.assertEqual(row.be_trigger_i, 1)
        self.assertEqual(row.be_effective_i, 2)
        self.assertEqual(row.deferred_closes, 1)
        self.assertEqual(row.exit_price, 10015)
        self.assertEqual(row.reason, 5)
        self.assertEqual(row.net_r, -5)

    def test_historical_market_precision_preserves_entry_and_stop_grid(self):
        rows, _, _ = replay(
            [[.0000015348]*4, [.0000015348, .000001536, .0000015331, .000001535],
             [.000001535, .000001536, .000001532, .000001534]],
            [(0, 1, .000001533, 2)], tick=1e-9, market_tick=1e-10,
        )
        self.assertEqual(rows.iloc[0].entry, .0000015348)
        self.assertEqual(rows.iloc[0].exit_price, .000001533)
        self.assertEqual(rows.iloc[0].reason, 1)


if __name__ == "__main__":
    unittest.main()
