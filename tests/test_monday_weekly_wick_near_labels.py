from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_weekly_wick_near_labels as near
from yoyo.evaluation.monday_weekly_wick_census3r import HOUR_MS, run_3r_outcomes


def _event(event_id: str, *, side: int, side_name: str, entry: float,
           stop: float, week_low: float, week_high: float) -> dict:
    return {
        "event_id": event_id,
        "symbol": "TESTUSDT",
        "week": "2026-03-09",
        "side": side,
        "side_name": side_name,
        "tf": 1,
        "bar_ms": 1_700_000_000_000,
        "close_ms": 1_700_000_000_000 + HOUR_MS,
        "first_breach_ms": 1_700_000_000_000,
        "anchor_start_ms": 1_700_000_000_000 - 24 * HOUR_MS,
        "anchor_ready_ms": 1_700_000_000_000 - 23 * HOUR_MS,
        "expires_ms": 1_700_000_000_000 + 100 * HOUR_MS,
        "entry_i": 5,
        "entry_ms": 1_700_000_000_000 + 5 * HOUR_MS,
        "entry": entry,
        "stop": stop,
        "risk_frac": abs(entry - stop) / entry,
        "week_low": week_low,
        "week_high": week_high,
        "status": "entered",
    }


class MondayWeeklyWickNearLabelTests(unittest.TestCase):
    def test_near_edges_and_ineligible_entries_are_classified(self):
        rows = [
            _event("lower", side=1, side_name="lower", entry=97.0,
                   stop=95.0, week_low=100.0, week_high=110.0),
            _event("upper", side=-1, side_name="upper", entry=105.0,
                   stop=107.0, week_low=90.0, week_high=100.0),
            _event("lower_at_entry", side=1, side_name="lower", entry=100.0,
                   stop=98.0, week_low=100.0, week_high=110.0),
            _event("upper_behind", side=-1, side_name="upper", entry=105.0,
                   stop=107.0, week_low=90.0, week_high=106.0),
            _event("lower_invalid", side=1, side_name="lower", entry=97.0,
                   stop=95.0, week_low=np.nan, week_high=110.0),
        ]
        rows[-1]["status"] = "entered"
        not_entered = dict(rows[0], event_id="not_entered", status="confirmed_no_next_open")
        groups, counts = near.prepare_entered_events(pd.DataFrame(rows + [not_entered]))

        self.assertEqual(counts["eligible_entered_rows"], 2)
        self.assertEqual(counts["skipped_not_entered_rows"], 1)
        self.assertEqual(counts["skipped_target_at_or_behind_entry_rows"], 2)
        self.assertEqual(counts["skipped_invalid_target_rows"], 1)
        self.assertEqual(
            {row["event_id"]: row["target_price"] for row in groups["TESTUSDT"]},
            {"lower": 100.0, "upper": 100.0},
        )

    def test_symbol_cache_reuses_exact_frozen_near_rows(self):
        event = _event("TESTUSDT/2026-03-09/lower", side=1, side_name="lower",
                       entry=97.0, stop=95.0, week_low=100.0, week_high=110.0)
        event["target_price"] = 100.0
        signature = {"path": "input.csv.gz", "size_bytes": 1, "mtime_ns": 2}
        tape_loader = Mock(return_value=np.zeros((1, 5)))
        outcome_runner = Mock(return_value=[{
            "label_id": f"{event['event_id']}/near_week_edge",
            "event_id": event["event_id"], "symbol": "TESTUSDT", "week": "2026-03-09",
            "side": 1, "tf": 1, "bar_ms": event["bar_ms"], "close_ms": event["close_ms"],
            "entry_ms": event["entry_ms"], "outcome": "tp", "target": "near_week_edge",
            "net_r": 1.48,
            "exit_ms": event["entry_ms"], "resolution": "h1",
            "target_price": 100.0, "entry": 97.0, "stop": 95.0, "risk_abs": 2.0,
            "exit_price": 100.0, "gross_r": 1.5, "fees_paid": 0.1,
        }])

        with tempfile.TemporaryDirectory() as temp_dir:
            previous = near.DATA_CACHE
            near.DATA_CACHE = Path(temp_dir)
            try:
                first, source_first = near._run_symbol(
                    "TESTUSDT", [event], signature, tape_loader=tape_loader,
                    outcome_runner=outcome_runner, minute_lookup_fn=None,
                )
                second, source_second = near._run_symbol(
                    "TESTUSDT", [event], signature, tape_loader=tape_loader,
                    outcome_runner=outcome_runner, minute_lookup_fn=None,
                )
            finally:
                near.DATA_CACHE = previous

        self.assertEqual(source_first, "computed")
        self.assertEqual(source_second, "cache")
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        tape_loader.assert_called_once_with("TESTUSDT")
        outcome_runner.assert_called_once()
        self.assertEqual(
            outcome_runner.call_args.kwargs["target_price_overrides"],
            {event["event_id"]: 100.0},
        )

    def test_omitting_override_preserves_the_original_3r_target(self):
        start = 1_700_000_000_000 // HOUR_MS * HOUR_MS
        ts = start + np.arange(16, dtype=np.int64) * HOUR_MS
        raw = np.column_stack((ts, np.full(16, 100.0), np.full(16, 101.0),
                               np.full(16, 99.0), np.full(16, 100.0)))
        raw[5] = [ts[5], 100.0, 107.0, 99.0, 106.0]
        event = _event("TESTUSDT/2026-03-09/lower", side=1, side_name="lower",
                       entry=100.0, stop=98.0, week_low=101.0, week_high=110.0)
        event.update(entry_i=5, entry_ms=int(ts[5]), bar_ms=int(ts[4]),
                     close_ms=int(ts[5]), first_breach_ms=int(ts[4]),
                     anchor_start_ms=int(ts[0]), anchor_ready_ms=int(ts[1]),
                     expires_ms=int(ts[10]))

        labels = run_3r_outcomes(raw, [event], minute_lookup_fn=None)

        self.assertEqual(labels[0]["target"], "3R")
        self.assertEqual(labels[0]["target_price"], 106.0)
        self.assertEqual(labels[0]["outcome"], "tp")

    def test_explicit_override_changes_only_the_target_barrier(self):
        start = 1_700_000_000_000 // HOUR_MS * HOUR_MS
        ts = start + np.arange(16, dtype=np.int64) * HOUR_MS
        raw = np.column_stack((ts, np.full(16, 100.0), np.full(16, 101.0),
                               np.full(16, 99.0), np.full(16, 100.0)))
        raw[5] = [ts[5], 100.0, 107.0, 99.0, 106.0]
        event = _event("TESTUSDT/2026-03-09/lower", side=1, side_name="lower",
                       entry=100.0, stop=98.0, week_low=104.0, week_high=110.0)
        event.update(entry_i=5, entry_ms=int(ts[5]), bar_ms=int(ts[4]),
                     close_ms=int(ts[5]), first_breach_ms=int(ts[4]),
                     anchor_start_ms=int(ts[0]), anchor_ready_ms=int(ts[1]),
                     expires_ms=int(ts[10]))

        labels = run_3r_outcomes(
            raw, [event], minute_lookup_fn=None,
            target_price_overrides={event["event_id"]: 104.0},
        )

        self.assertEqual(labels[0]["target"], "near_week_edge")
        self.assertEqual(labels[0]["target_price"], 104.0)
        self.assertEqual(labels[0]["label_id"], f"{event['event_id']}/near_week_edge")
        self.assertEqual(labels[0]["outcome"], "tp")
        self.assertEqual(labels[0]["entry"], 100.0)
        self.assertEqual(labels[0]["stop"], 98.0)


if __name__ == "__main__":
    unittest.main()
