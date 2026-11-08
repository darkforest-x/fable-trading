"""Native market-open execution for standalone static brackets."""
import importlib.util
import unittest

import numpy as np
import pandas as pd

if importlib.util.find_spec("nautilus_trader") is None:
    raise unittest.SkipTest("Run with the isolated Nautilus runtime")

from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native


def replay(bars, entry_i, stop, target, end_i, path="OHLC"):
    frame = pd.DataFrame(
        bars,
        columns=["open", "high", "low", "close"],
        index=pd.date_range("2026-01-01", periods=len(bars), freq="60min", tz="UTC"),
    )
    entry = float(frame.open.iloc[entry_i])
    entries = pd.DataFrame([dict(
        trade_id="market-open-test", entry_i=entry_i, side=1, entry=entry,
        stop=float(stop), risk=entry-float(stop), target_price=float(target), end_i=end_i,
    )])
    policy = ExitPolicy(
        trail_arm_r=float("inf"), opposite_hour_exit=False, round_trip_cost=0.,
        target_from_entry=True, intrabar_path=path, monotone_segments=True,
        fill_maker_fee=.0002, fill_taker_fee=.0005,
    )
    return run_native(
        frame, np.full(len(frame), np.nan), np.zeros(len(frame)), entries, policy,
        tick=1., symbol="TEST", execution_minutes=60, entry_order_mode="market_open",
    )


class MarketOpenTests(unittest.TestCase):
    def test_entry_uses_gapped_open_and_does_not_trade_pre_entry_gap(self):
        rows, fills, audit = replay(
            [[99, 101, 98, 100], [110, 115, 109, 112], [112, 113, 111, 112]],
            entry_i=1, stop=105, target=120, end_i=2,
        )
        row = rows.iloc[0]
        entry_fill = fills[fills.phase == "entry"].iloc[0]
        self.assertEqual(entry_fill.price, 110.)
        self.assertEqual(entry_fill.intrabar_phase, 0)
        self.assertEqual(row.entry, 110.)
        self.assertEqual(row.entry_time, "2026-01-01 00:00:00+00:00")
        self.assertEqual(row.exit_time, "2026-01-01 02:00:00+00:00")
        self.assertEqual(row.reason, 0)
        self.assertAlmostEqual(row.peak_r, 1.)
        self.assertEqual(audit["entry_order_mode"], "market_open")
        self.assertIn("synthetic path timestamps", audit["entry_time_basis"])

    def test_entry_bar_double_touch_follows_explicit_path_and_marks_ambiguity(self):
        bars = [[100, 100, 100, 100], [100, 106, 94, 100], [100, 100, 100, 100]]
        oh, _, _ = replay(bars, entry_i=1, stop=95, target=105, end_i=2, path="OHLC")
        ol, _, _ = replay(bars, entry_i=1, stop=95, target=105, end_i=2, path="OLHC")
        self.assertEqual(oh.iloc[0].reason, 4)
        self.assertEqual(oh.iloc[0].exit_price, 105.)
        self.assertEqual(ol.iloc[0].reason, 1)
        self.assertEqual(ol.iloc[0].exit_price, 95.)
        self.assertEqual(oh.iloc[0].ambiguous, 1)
        self.assertEqual(ol.iloc[0].ambiguous, 1)

    def test_same_entry_bar_timeout_exits_at_bar_close(self):
        rows, fills, _ = replay(
            [[100, 100, 100, 100], [100, 104, 98, 102]],
            entry_i=1, stop=95, target=110, end_i=1,
        )
        row = rows.iloc[0]
        self.assertEqual(row.exit_i, row.entry_i)
        self.assertEqual(row.reason, 0)
        self.assertEqual(row.exit_price, 102.)
        self.assertEqual(row.exit_time, "2026-01-01 01:00:00+00:00")
        self.assertEqual(len(fills), 2)


if __name__ == "__main__":
    unittest.main()
