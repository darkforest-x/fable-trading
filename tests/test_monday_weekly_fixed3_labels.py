from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_weekly_fixed3_labels as fixed3
from yoyo.evaluation.monday_weekly_wick_census3r import HOUR_MS


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
        "risk_abs": abs(entry - stop),
        "week_low": week_low,
        "week_high": week_high,
        "status": "entered",
    }


class MondayWeeklyFixed3LabelTests(unittest.TestCase):
    def test_prepare_replaces_frozen_risk_for_long_and_short(self):
        rows = [
            _event("long", side=1, side_name="lower", entry=97.0,
                   stop=96.03, week_low=100.0, week_high=110.0),
            _event("short", side=-1, side_name="upper", entry=105.0,
                   stop=106.05, week_low=90.0, week_high=100.0),
            dict(_event("not_entered", side=1, side_name="lower", entry=97.0,
                        stop=96.03, week_low=100.0, week_high=110.0),
                 status="confirmed_no_next_open"),
        ]

        groups, counts = fixed3.prepare_fixed3_events(pd.DataFrame(rows))

        self.assertEqual(counts["eligible_entered_rows"], 2)
        self.assertEqual(counts["skipped_not_entered_rows"], 1)
        by_id = {event["event_id"]: event for events in groups.values() for event in events}
        self.assertEqual(set(by_id), {"long", "short"})
        self.assertAlmostEqual(by_id["long"]["stop"], 97.0 * 0.97)
        self.assertAlmostEqual(by_id["short"]["stop"], 105.0 * 1.03)
        for event in by_id.values():
            self.assertAlmostEqual(event["risk_frac"], 0.03)
            self.assertNotIn("risk_abs", event)
            self.assertEqual(event["target_price"], 100.0)

    def test_symbol_run_uses_new_stop_and_near_target_with_existing_adapter(self):
        event = _event("TESTUSDT/2026-03-09/lower", side=1, side_name="lower",
                       entry=100.0, stop=98.0, week_low=104.0, week_high=110.0)
        event.update(stop=97.0, risk_frac=0.03, target_price=104.0)
        signature = {"path": "frozen.csv.gz", "size_bytes": 1, "mtime_ns": 2}
        reference_signature = {"path": "near.csv.gz", "size_bytes": 3, "mtime_ns": 4}
        tape = np.zeros((1, 5), dtype=np.float64)
        tape_loader = Mock(return_value=tape)
        minute_lookup = Mock(return_value=None)
        outcome_runner = Mock(return_value=[{
            "label_id": f"{event['event_id']}/near_week_edge",
            "event_id": event["event_id"], "symbol": "TESTUSDT", "week": "2026-03-09",
            "side": 1, "tf": 1, "bar_ms": event["bar_ms"], "close_ms": event["close_ms"],
            "first_breach_ms": event["first_breach_ms"], "anchor_start_ms": event["anchor_start_ms"],
            "anchor_ready_ms": event["anchor_ready_ms"], "expires_ms": event["expires_ms"],
            "entry_ms": event["entry_ms"], "entry": 100.0, "stop": 97.0,
            "risk_abs": 3.0, "risk_frac": 0.03,
            "target": "near_week_edge", "target_price": 104.0, "outcome": "tp",
            "resolution": "h1", "ambiguous": False, "minute_source": "not_needed",
            "exit_ms": event["entry_ms"], "exit_price": 104.0,
            "gross_r": 4.0 / 3.0, "net_r": 1.0, "net_return": 0.03,
            "entry_fee_rate": 0.0005, "exit_fee_rate": 0.0002,
            "entry_fee": 0.05, "exit_fee": 0.0208, "fees_paid": 0.0708,
            "mark_ms": event["entry_ms"], "mark_price": 100.0,
            "mark_gross_r": 0.0, "mark_net_r": -0.0167,
            "censor_reason": None, "quantity": 1.0,
            "engine": "VectorBT.from_orders+monday_wick_r_engine.first_hit",
        }])

        with tempfile.TemporaryDirectory() as temp_dir:
            old_cache = fixed3.DATA_CACHE
            fixed3.DATA_CACHE = Path(temp_dir)
            try:
                labels, source = fixed3._run_symbol(
                    "TESTUSDT", [event], signature, reference_signature,
                    tape_loader=tape_loader, outcome_runner=outcome_runner,
                    minute_lookup_fn=minute_lookup,
                )
            finally:
                fixed3.DATA_CACHE = old_cache

        self.assertEqual(source, "computed")
        self.assertEqual(tuple(labels.columns), fixed3.OUTPUT_COLUMNS)
        call = outcome_runner.call_args
        self.assertIs(call.args[0], tape)
        self.assertEqual(call.kwargs["target_price_overrides"], {event["event_id"]: 104.0})
        self.assertEqual(call.kwargs["target_label"], "near_week_edge")
        self.assertIs(call.kwargs["minute_lookup_fn"], minute_lookup)
        self.assertEqual(call.args[1][0]["stop"], 97.0)
        self.assertEqual(call.args[1][0]["risk_frac"], 0.03)
        self.assertAlmostEqual(float(labels.iloc[0].risk_abs), 3.0)

    def test_run_output_keeps_prior_columns_and_does_not_write_config(self):
        labels = pd.DataFrame([{
            **{column: np.nan for column in fixed3.OUTPUT_COLUMNS},
            "event_id": "row-1", "outcome": "censored", "resolution": "h1",
        }], columns=fixed3.OUTPUT_COLUMNS)
        counts = {"eligible_entered_rows": 1, "skipped_rows": 0}
        with tempfile.TemporaryDirectory() as temp_dir:
            old_exp = fixed3.EXP
            fixed3.EXP = Path(temp_dir)
            config_path = fixed3.EXP / "config.json"
            config_path.write_text('{"owner_config":true}\n')
            try:
                receipt = fixed3._write_run_outputs(
                    labels, counts,
                    parent_receipt={"experiment": "source"},
                    signature={"path": "input.csv.gz"},
                    reference_signature={"path": "near.csv.gz"},
                    workers=8, elapsed_seconds=1.0, cache_hits=0, symbol_count=1,
                )
                output = pd.read_csv(fixed3.EXP / "outcomes.csv.gz")
                receipt_file = fixed3.EXP / "label_receipt.json"
                config_contents = config_path.read_text()
            finally:
                fixed3.EXP = old_exp

        self.assertEqual(tuple(output.columns), fixed3.OUTPUT_COLUMNS)
        self.assertEqual(config_contents, '{"owner_config":true}\n')
        self.assertEqual(receipt["experiment"], fixed3.EXPERIMENT_ID)
        self.assertEqual(receipt_file.name, "label_receipt.json")
        self.assertNotIn("config.json", {receipt_file.name, "outcomes.csv.gz"})


if __name__ == "__main__":
    unittest.main()
