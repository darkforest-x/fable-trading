"""Small input fixtures for native-timeframe and weekly-calendar guarantees."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_limit_reclaim_inputs as inputs


class MondayLimitReclaimInputTests(unittest.TestCase):
    def test_five_minute_crypto_input_is_not_upsampled(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bars.csv"
            opens = pd.date_range("2026-10-19T04:00:00Z", periods=2, freq="5min")
            pd.DataFrame({
                "ts": opens.as_unit("ns").asi8 // 1_000_000,
                "open": [100.0, 101.0],
                "high": [102.0, 103.0],
                "low": [99.0, 100.0],
                "close": [101.0, 102.0],
                "volume": [5.0, 6.0],
            }).to_csv(path, index=False)
            job = dict(job_id="TESTUSDT-5m", symbol="TESTUSDT", group="crypto_5m",
                       execution_minutes=5, confirm_minutes=[5, 15, 60], paths=[str(path)])

            frame, quality = inputs.load_job(job)

        self.assertEqual(len(frame), 2)
        self.assertEqual(list(np.diff(frame.index.asi8)), [pd.Timedelta(minutes=5).value])
        self.assertEqual(frame.attrs["execution_minutes"], 5)
        self.assertEqual(quality["execution_minutes"], 5)

    def test_gold_conflict_excludes_every_minute_in_affected_five_minute_bin(self):
        index = pd.date_range("2026-10-19T04:00:00Z", periods=15, freq="min")
        raw = pd.DataFrame(
            [[100.0, 101.0, 99.0, 100.0]] * len(index),
            columns=inputs.OHLC, index=index,
        )
        conflict = raw.iloc[[7]].copy()
        conflict.loc[:, "high"] = 102.0
        identical_duplicate = raw.iloc[[1]].copy()

        frame, quality = inputs._clean_gold_minutes(
            pd.concat([raw, conflict, identical_duplicate])
        )

        self.assertEqual(len(frame), 10)
        self.assertEqual(frame.index[4], index[4])
        self.assertEqual(frame.index[5], index[10])
        self.assertEqual(frame.quality_segment.tolist(), [0] * 5 + [1] * 5)
        self.assertTrue(frame.volume.isna().all())
        self.assertEqual(quality["conflicting_minutes"], 1)
        self.assertEqual(quality["excluded_five_minute_bins"], 1)
        self.assertEqual(quality["blocked_monday_weeks"], ["2026-10-19"])

    def test_crypto_monday_and_monitor_ranges_use_new_york_dst_boundaries(self):
        monday_local = pd.Timestamp("2026-03-02 00:00:00", tz=inputs.TZ)
        monday_utc = monday_local.tz_convert("UTC")
        monday = pd.date_range(monday_utc, periods=288, freq="5min")
        tuesday_utc = (monday_local + pd.DateOffset(days=1)).tz_convert("UTC")
        monitor = pd.DatetimeIndex([
            tuesday_utc,
            tuesday_utc + pd.Timedelta(minutes=5),
            tuesday_utc + pd.Timedelta(minutes=15),
        ])
        index = monday.append(monitor)
        frame = pd.DataFrame({
            "open": 100.0,
            "high": 102.0,
            "low": 98.0,
            "close": 101.0,
            "volume": 1.0,
        }, index=index)
        frame.attrs["group"] = "crypto_5m"

        weeks = inputs.weekly_context(frame, 5)

        self.assertEqual(len(weeks), 1)
        week = weeks[0]
        self.assertTrue(week["monday_valid"])
        self.assertEqual(week["monday_high"], 102.0)
        self.assertEqual(week["monday_low"], 98.0)
        self.assertEqual(week["monday_start_utc"], "2026-03-02 05:00:00+00:00")
        self.assertEqual(week["next_monday_utc"], "2026-03-09 04:00:00+00:00")
        self.assertEqual(week["monitor_start_i"], 288)
        self.assertEqual(week["monitor_end_i_exclusive"], 290)
        self.assertEqual(week["monitor_cutoff_cause"], "missing_execution_bar")
        self.assertEqual(week["monitor_cutoff_time"], "2026-03-03 05:10:00+00:00")
        self.assertEqual(week["monitor_gap_count"], 1)
        self.assertEqual(week["monitor_gaps"][0]["reason"], "missing_execution_bar")

    def test_gold_uses_existing_observed_monday_bin_minimum_and_keeps_gaps(self):
        monday_local = pd.Timestamp("2026-10-19 00:00:00", tz=inputs.TZ)
        monday_utc = monday_local.tz_convert("UTC")
        monday = pd.date_range(monday_utc, periods=240 * 5, freq="min")
        tuesday_utc = (monday_local + pd.DateOffset(days=1)).tz_convert("UTC")
        index = monday.append(pd.DatetimeIndex([tuesday_utc, tuesday_utc + pd.Timedelta(minutes=1)]))
        frame = pd.DataFrame({
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": np.nan,
        }, index=index)
        frame.attrs["group"] = "spot_gold"

        week = inputs.weekly_context(frame, 1)[0]

        self.assertTrue(week["monday_valid"])
        self.assertEqual(week["observed_monday_5m_bins"], 240)
        self.assertEqual(week["monitor_start_i"], len(monday))
        self.assertEqual(week["monitor_start_delay_minutes"], 0.0)
        self.assertEqual(week["monitor_cutoff_cause"], "source_end")


if __name__ == "__main__":
    unittest.main()
