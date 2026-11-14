"""Contract for the owner 2026-11-09 hourly-cloud exit on 1H execution bars.

hour_exit_from_entry makes the confirmed hourly direction exit live from the
first bar after entry (no 5R trail arm), and is the one close-market mode
allowed on 1H bars. Real Nautilus orders; run in the isolated runtime.
"""
import unittest
import importlib.util

import numpy as np
import pandas as pd

if importlib.util.find_spec("nautilus_trader") is None:
    raise unittest.SkipTest("Run with the isolated Nautilus runtime, not the model-training venv")

from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native


def replay(bars, stop, hour, from_entry=True, minutes=60):
    frame = pd.DataFrame(bars, columns=["open", "high", "low", "close"],
                         index=pd.date_range("2026-01-01 01:00", periods=len(bars), freq=f"{minutes}min", tz="UTC"))
    entry = bars[0][3]
    entries = pd.DataFrame([dict(trade_id="t", entry_i=0, side=1, entry=entry, stop=stop,
                                 risk=entry - stop, end_i=len(bars) - 1)])
    policy = ExitPolicy(opposite_hour_exit=True, hour_exit_from_entry=from_entry, round_trip_cost=0.,
                        fill_maker_fee=.0002, fill_taker_fee=.0005)
    return run_native(frame, np.full(len(bars), np.nan), np.asarray(hour, float), entries, policy,
                      tick=1., symbol="TEST", execution_minutes=minutes)


class HourExitFromEntry(unittest.TestCase):
    def test_exit_at_first_bearish_hour_close_without_arm(self):
        rows, fills, _ = replay([[100, 100, 100, 100], [100, 103, 99, 102], [102, 104, 101, 103], [103, 103, 98, 99]],
                                stop=95, hour=[1, 1, -1, -1])
        r = rows.iloc[0]
        self.assertEqual(r.exit_i, 2)
        self.assertEqual(r.reason, 3)
        self.assertEqual(r.exit_price, 103)
        self.assertEqual(len(fills), 2)

    def test_resting_stop_wins_before_hour_close(self):
        rows, _, _ = replay([[100, 100, 100, 100], [100, 101, 94, 99], [99, 99, 98, 98]],
                            stop=95, hour=[1, -1, -1])
        r = rows.iloc[0]
        self.assertEqual(r.exit_i, 1)
        self.assertEqual(r.reason, 1)
        self.assertEqual(r.exit_price, 95)

    def test_legacy_hour_exit_still_waits_for_arm(self):
        with self.assertRaises(ValueError):
            replay([[100, 100, 100, 100], [100, 101, 99, 100]], stop=95, hour=[1, -1], from_entry=False)
        rows, _, _ = replay([[100, 100, 100, 100]] + [[100, 101, 99, 100]] * 3, stop=95, hour=[1, -1, -1, -1],
                            from_entry=False, minutes=5)
        self.assertEqual(rows.iloc[0].reason, 0)

    def test_flag_requires_opposite_hour_exit(self):
        with self.assertRaises(ValueError):
            ExitPolicy(opposite_hour_exit=False, hour_exit_from_entry=True)


if __name__ == "__main__":
    unittest.main()
