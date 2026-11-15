from __future__ import annotations

import unittest

import numpy as np

from yoyo.evaluation.monday_weekly_wick_census3r import (
    DAY_MS,
    HOUR_MS,
    NY,
    ny_week_window,
    run_3r_outcomes,
    scan_week,
)


def _weekly_tape(week: str, extra_hours: int = 24) -> tuple[np.ndarray, dict]:
    window = ny_week_window(week)
    start, expires = int(window["start_ms"]), int(window["expires_ms"])
    hours = (expires - start) // HOUR_MS + extra_hours
    ts = start + np.arange(hours, dtype=np.int64) * HOUR_MS
    raw = np.column_stack((ts, np.full(hours, 100.0), np.full(hours, 101.0),
                           np.full(hours, 99.0), np.full(hours, 100.0)))
    ready_i = int(np.searchsorted(ts, int(window["ready_ms"])))
    raw[:ready_i, 2] = 110.0
    raw[:ready_i, 3] = 90.0
    return raw, window


class MondayWeeklyWickCalendarTests(unittest.TestCase):
    def test_dst_week_window_uses_167_and_169_hour_expiries(self):
        spring = ny_week_window("2026-03-02")
        fall = ny_week_window("2026-10-26")
        self.assertEqual(spring["ready_ms"] - spring["start_ms"], DAY_MS)
        self.assertEqual(fall["ready_ms"] - fall["start_ms"], DAY_MS)
        self.assertEqual((spring["expires_ms"] - spring["start_ms"]) // HOUR_MS, 167)
        self.assertEqual((fall["expires_ms"] - fall["start_ms"]) // HOUR_MS, 169)

    def test_equality_does_not_consume_first_breach_and_h1_has_priority(self):
        raw, window = _weekly_tape("2026-03-09")
        ready_i = 24
        lower = 90.0 - 0.618 * 20.0
        raw[ready_i, 3] = lower  # equality is allowed before the strict breach.
        raw[ready_i + 1] = [raw[ready_i + 1, 0], 100.0, 101.0, lower - 1.0, 100.0]

        events, coverage = scan_week(
            "TESTUSDT", raw, "2026-03-09", cutoff_ms=int(window["expires_ms"])
        )
        lower_event = next(row for row in events if row["side_name"] == "lower")
        self.assertEqual(lower_event["status"], "entered")
        self.assertEqual(lower_event["first_breach_ms"], int(raw[ready_i + 1, 0]))
        self.assertEqual(lower_event["tf"], 1)
        self.assertEqual(coverage["anchor_status"], "ready")

    def test_falls_back_from_failed_h1_to_first_qualifying_four_hour_bar(self):
        raw, window = _weekly_tape("2026-03-09")
        ready_i = 24
        lower = 90.0 - 0.618 * 20.0
        # The first strict breach closes outside, so 1h fails. Its enclosing
        # 4h candle opens and closes inside, with a strict outside wick.
        raw[ready_i] = [raw[ready_i, 0], 100.0, 101.0, lower - 5.0, lower - 1.0]
        raw[ready_i + 1] = [raw[ready_i + 1, 0], lower - 1.0, 100.0, lower - 7.0, lower - 2.0]
        raw[ready_i + 2] = [raw[ready_i + 2, 0], lower - 2.0, 100.0, lower - 8.0, lower - 2.0]
        raw[ready_i + 3] = [raw[ready_i + 3, 0], lower - 2.0, 101.0, lower - 6.0, 100.0]

        events, _ = scan_week(
            "TESTUSDT", raw, "2026-03-09", cutoff_ms=int(window["expires_ms"])
        )
        lower_event = next(row for row in events if row["side_name"] == "lower")
        self.assertEqual(lower_event["state_1h"], "failed")
        self.assertEqual(lower_event["state_4h"], "wick")
        self.assertEqual(lower_event["state_12h"], "wick")
        self.assertEqual(lower_event["tf"], 4)
        self.assertEqual(lower_event["status"], "entered")

    def test_first_gap_terminates_week_scan(self):
        raw, window = _weekly_tape("2026-03-09")
        ready_i = 24
        lower = 90.0 - 0.618 * 20.0
        breach_i = ready_i + 2
        raw[breach_i, 3] = lower - 1.0
        raw = np.delete(raw, ready_i + 1, axis=0)

        events, coverage = scan_week(
            "TESTUSDT", raw, "2026-03-09", cutoff_ms=int(window["expires_ms"])
        )
        lower_event = next(row for row in events if row["side_name"] == "lower")
        self.assertEqual(lower_event["status"], "no_breach")
        self.assertFalse(coverage["active_window_complete"])

    def test_gap_after_expiry_does_not_invalidate_week_coverage(self):
        raw, window = _weekly_tape("2026-03-09", extra_hours=30)
        gap_i = int(np.searchsorted(raw[:, 0], int(window["expires_ms"]))) + 2
        raw = np.delete(raw, gap_i, axis=0)
        events, coverage = scan_week(
            "TESTUSDT", raw, "2026-03-09", cutoff_ms=int(raw[-1, 0] + HOUR_MS)
        )
        self.assertTrue(coverage["active_window_complete"])
        self.assertEqual(len(events), 2)

    def test_close_at_expiry_is_allowed_when_next_hour_open_exists(self):
        raw, window = _weekly_tape("2026-03-09", extra_hours=4)
        lower = 90.0 - 0.618 * 20.0
        at_expiry = int(np.searchsorted(raw[:, 0], int(window["expires_ms"])))
        raw[at_expiry - 1] = [raw[at_expiry - 1, 0], 100.0, 101.0, lower - 1.0, 100.0]

        events, _ = scan_week(
            "TESTUSDT", raw, "2026-03-09", cutoff_ms=int(raw[-1, 0] + HOUR_MS)
        )
        lower_event = next(row for row in events if row["side_name"] == "lower")
        self.assertEqual(lower_event["status"], "entered")
        self.assertEqual(lower_event["close_ms"], window["expires_ms"])
        self.assertEqual(lower_event["entry_ms"], window["expires_ms"])


class MondayWeeklyWick3RTests(unittest.TestCase):
    @staticmethod
    def _event(raw: np.ndarray, expires_ms: int) -> dict:
        entry_i = 5
        return {
            "event_id": "TESTUSDT/2026-03-09/lower",
            "symbol": "TESTUSDT", "week": "2026-03-09", "side": 1,
            "tf": 1, "bar_ms": int(raw[4, 0]), "close_ms": int(raw[5, 0]),
            "first_breach_ms": int(raw[4, 0]), "anchor_start_ms": int(raw[0, 0]),
            "anchor_ready_ms": int(raw[1, 0]), "expires_ms": int(expires_ms),
            "entry_i": entry_i, "entry_ms": int(raw[entry_i, 0]),
            "entry": 100.0, "stop": 98.0, "risk_frac": 0.02,
        }

    def test_entry_hour_is_included_and_only_three_r_is_emitted(self):
        start = 1_700_000_000_000 // HOUR_MS * HOUR_MS
        ts = start + np.arange(16, dtype=np.int64) * HOUR_MS
        raw = np.column_stack((ts, np.full(16, 100.0), np.full(16, 101.0),
                               np.full(16, 99.0), np.full(16, 100.0)))
        raw[5] = [ts[5], 100.0, 107.0, 99.0, 106.0]
        event = self._event(raw, int(ts[10]))

        labels = run_3r_outcomes(raw, [event], minute_lookup_fn=None)
        self.assertEqual(len(labels), 1)
        self.assertEqual(labels[0]["target"], "3R")
        self.assertEqual(labels[0]["outcome"], "tp")
        self.assertEqual(labels[0]["exit_ms"], int(ts[5]))

    def test_h1_double_touch_is_minute_resolved_and_fee_reconciled(self):
        start = 1_700_000_000_000 // HOUR_MS * HOUR_MS
        ts = start + np.arange(16, dtype=np.int64) * HOUR_MS
        raw = np.column_stack((ts, np.full(16, 100.0), np.full(16, 101.0),
                               np.full(16, 99.0), np.full(16, 100.0)))
        raw[5] = [ts[5], 100.0, 107.0, 97.0, 101.0]
        event = self._event(raw, int(ts[10]))
        minute_ts = ts[5] + np.arange(60, dtype=np.int64) * 60_000
        minutes = np.column_stack((minute_ts, np.full(60, 100.0),
                                   np.full(60, 101.0), np.full(60, 99.0),
                                   np.full(60, 100.0)))
        minutes[1] = [minute_ts[1], 100.0, 107.0, 99.0, 106.5]
        minutes[2] = [minute_ts[2], 106.5, 106.5, 97.0, 100.0]
        minutes[-1] = [minute_ts[-1], 100.0, 101.0, 100.0, 101.0]

        labels = run_3r_outcomes(
            raw, [event], minute_lookup_fn=lambda _symbol, _bar: minutes
        )
        self.assertEqual(labels[0]["outcome"], "tp")
        self.assertEqual(labels[0]["resolution"], "1m")
        self.assertEqual(labels[0]["exit_ms"], int(minute_ts[1]))
        self.assertAlmostEqual(labels[0]["net_r"], (6.0 - 0.05 - 106.0 * 0.0002) / 2.0)

    def test_missing_dual_touch_minutes_keep_conservative_stop_first_fill(self):
        start = 1_700_000_000_000 // HOUR_MS * HOUR_MS
        ts = start + np.arange(16, dtype=np.int64) * HOUR_MS
        raw = np.column_stack((ts, np.full(16, 100.0), np.full(16, 101.0),
                               np.full(16, 99.0), np.full(16, 100.0)))
        raw[5] = [ts[5], 100.0, 107.0, 97.0, 101.0]
        event = self._event(raw, int(ts[10]))

        labels = run_3r_outcomes(raw, [event], minute_lookup_fn=lambda _s, _bar: None)
        self.assertEqual(labels[0]["outcome"], "sl")
        self.assertEqual(labels[0]["resolution"], "h1_fallback")
        self.assertTrue(labels[0]["ambiguous"])
        self.assertEqual(labels[0]["minute_source"], "minute_source_unknown")

    def test_open_path_can_settle_after_ny_week_expiry(self):
        start = 1_700_000_000_000 // HOUR_MS * HOUR_MS
        ts = start + np.arange(16, dtype=np.int64) * HOUR_MS
        raw = np.column_stack((ts, np.full(16, 100.0), np.full(16, 101.0),
                               np.full(16, 99.0), np.full(16, 100.0)))
        raw[12] = [ts[12], 100.0, 107.0, 99.0, 106.0]
        event = self._event(raw, int(ts[8]))

        labels = run_3r_outcomes(raw, [event], minute_lookup_fn=None)
        self.assertEqual(labels[0]["outcome"], "tp")
        self.assertGreater(labels[0]["exit_ms"], event["expires_ms"])


if __name__ == "__main__":
    unittest.main()
