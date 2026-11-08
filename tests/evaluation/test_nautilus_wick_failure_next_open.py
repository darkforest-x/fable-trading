"""Native two-close wick-failure exits for close-market 5m reclaim positions."""
import importlib.util
import unittest

import numpy as np
import pandas as pd

if importlib.util.find_spec("nautilus_trader") is None:
    raise unittest.SkipTest("Run with the isolated Nautilus runtime, not the model-training venv")

from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native


def replay(bars, *, side=1, path="OHLC", hour=None, execution_minutes=5):
    frame = pd.DataFrame(
        bars, columns=["open", "high", "low", "close"],
        index=pd.date_range("2026-01-01 00:05", periods=len(bars), freq="5min", tz="UTC"),
    )
    entry = 100.
    entries = pd.DataFrame([dict(
        trade_id="wick", entry_i=0, end_i=len(frame)-1, entry=entry, side=side,
        stop=80. if side > 0 else 120., risk=entry,
        target_price=140. if side > 0 else 60., reclaim_minutes=20,
    )])
    policy = ExitPolicy(
        target_from_entry=True, reclaim_next_bar=True, reclaim_backstop=True,
        opposite_hour_exit=False, breakeven_includes_cost=False,
        intrabar_path=path, monotone_segments=True, wick_failure_next_open=True,
        round_trip_cost=0.,
    )
    return frame, run_native(
        frame, np.full(len(frame), np.nan), np.zeros(len(frame)) if hour is None else hour,
        entries, policy, tick=.1, symbol="TEST", execution_minutes=execution_minutes,
    )


class WickFailureNextOpenTests(unittest.TestCase):
    def test_long_and_short_exit_at_the_next_open_on_both_monotone_paths(self):
        scenarios = [
            (1, [[100, 105, 90, 100], [100, 102, 88, 89], [89, 92, 86, 87],
                 [87, 89, 85, 86], [86, 87, 85, 86]], 87.),
            (-1, [[100, 110, 95, 100], [100, 112, 98, 111], [111, 114, 108, 112],
                  [112, 115, 110, 113], [113, 115, 110, 114]], 112.),
        ]
        for side, bars, expected_open in scenarios:
            for path in ("OHLC", "OLHC"):
                with self.subTest(side=side, path=path):
                    frame, (rows, fills, _) = replay(bars, side=side, path=path)
                    row = rows.iloc[0]
                    self.assertEqual((row.reason, row.exit_i), (15, 3))
                    self.assertEqual(row.exit_price, expected_open)
                    self.assertEqual(row.exit_time, str(frame.index[3]-pd.Timedelta(minutes=5)))
                    self.assertEqual(row.framework_exit_time, str(frame.index[3]))
                    exit_fill = fills.loc[fills.phase.eq("15")].iloc[0]
                    self.assertEqual(exit_fill.price, expected_open)
                    self.assertEqual(exit_fill.intrabar_phase, 0)

    def test_equality_or_only_one_beyond_close_does_not_trigger(self):
        cases = [
            # Equality on the first close breaks strictness; the next pair no
            # longer contains two closes below the entry bar's low.
            [[100, 105, 90, 100], [100, 102, 88, 90], [90, 92, 86, 89],
             [89, 100, 89, 100], [100, 101, 99, 100]],
            # One close beyond the wick followed by a reset close is insufficient.
            [[100, 105, 90, 100], [100, 102, 88, 89], [89, 100, 89, 100],
             [100, 101, 99, 100]],
        ]
        for bars in cases:
            with self.subTest(bars=bars):
                _, (rows, _, _) = replay(bars)
                self.assertNotEqual(int(rows.iloc[0].reason), 15)

    def test_gap_resets_the_close_pair(self):
        bars = [[100, 105, 90, 100], [100, 102, 95, 100], [100, 102, 89, 89],
                [89, 90, 87, 88], [88, 89, 86, 87]]
        frame = pd.DataFrame(
            bars, columns=["open", "high", "low", "close"],
            index=pd.to_datetime([
                "2026-01-01 00:05Z", "2026-01-01 00:10Z", "2026-01-01 00:20Z",
                "2026-01-01 00:25Z", "2026-01-01 00:30Z",
            ]),
        )
        entries = pd.DataFrame([dict(
            trade_id="wick", entry_i=0, end_i=4, entry=100., side=1, stop=80., risk=100.,
            target_price=140., reclaim_minutes=5, reclaim_ref=80.,
        )])
        # The scheduled reclaim moves protection to 80, below the entry wick.
        # The gap interrupts a one-close streak; two new continuous closes
        # beyond the wick then trigger at the following opening print.
        policy = ExitPolicy(
            target_from_entry=True, reclaim_next_bar=True, reclaim_backstop=True,
            opposite_hour_exit=False, breakeven_includes_cost=False, intrabar_path="OHLC",
            monotone_segments=True, wick_failure_next_open=True, round_trip_cost=0.,
        )
        rows, _, _ = run_native(frame, np.full(5, np.nan), np.zeros(5), entries, policy,
                                tick=.1, symbol="TEST", execution_minutes=5)
        self.assertEqual((rows.iloc[0].reason, rows.iloc[0].exit_i,
                          rows.iloc[0].reclaim_status), (15, 4, "reclaimed"))

    def test_opening_gap_stop_has_native_priority(self):
        cases = [
            ([[100, 105, 90, 100], [100, 102, 88, 89], [89, 92, 86, 87],
              [75, 78, 74, 77], [77, 78, 76, 77]], (1, 3, 75.)),
            ([[100, 105, 90, 100], [100, 102, 88, 89], [89, 92, 86, 87],
              [145, 147, 143, 144], [144, 146, 142, 145]], (4, 3, 140.)),
        ]
        for bars, expected in cases:
            with self.subTest(expected=expected):
                _, (rows, _, _) = replay(bars, path="OHLC")
                row = rows.iloc[0]
                self.assertEqual((row.reason, row.exit_i, row.exit_price), expected)

    def test_original_reclaim_decision_close_remains_authoritative(self):
        bars = [[100, 105, 90, 100], [100, 102, 95, 99], [99, 101, 95, 98],
                [98, 100, 94, 99], [99, 100, 98, 99]]
        # A 5m reclaim window makes the decision close the first bar after
        # entry: it fails at that close before any two-close wick trigger.
        frame = pd.DataFrame(
            bars, columns=["open", "high", "low", "close"],
            index=pd.date_range("2026-01-01 00:05", periods=len(bars), freq="5min", tz="UTC"),
        )
        entries = pd.DataFrame([dict(
            trade_id="wick", entry_i=0, end_i=4, entry=100., side=1, stop=80., risk=100.,
            target_price=140., reclaim_minutes=5,
        )])
        policy = ExitPolicy(
            target_from_entry=True, reclaim_next_bar=True, reclaim_backstop=True,
            opposite_hour_exit=False, breakeven_includes_cost=False, intrabar_path="OHLC",
            monotone_segments=True, wick_failure_next_open=True, round_trip_cost=0.,
        )
        rows, _, _ = run_native(frame, np.full(5, np.nan), np.zeros(5), entries, policy,
                                tick=.1, symbol="TEST", execution_minutes=5)
        row = rows.iloc[0]
        self.assertEqual((row.reason, row.exit_i, row.reclaim_status), (11, 1, "failed"))

    def test_option_rejects_non_reclaim_policy_hourly_exit_or_non_5m_execution(self):
        with self.assertRaises(ValueError):
            ExitPolicy(wick_failure_next_open=True)
        bars = [[100, 105, 90, 100], [100, 102, 88, 89], [89, 92, 86, 87],
                [87, 89, 85, 86], [86, 87, 85, 86]]
        with self.assertRaises(ValueError):
            replay(bars, execution_minutes=1)
        with self.assertRaises(ValueError):
            ExitPolicy(target_from_entry=True, reclaim_next_bar=True, reclaim_backstop=True,
                       wick_failure_next_open=True, intrabar_path="OHLC", monotone_segments=True)


if __name__ == "__main__":
    unittest.main()
