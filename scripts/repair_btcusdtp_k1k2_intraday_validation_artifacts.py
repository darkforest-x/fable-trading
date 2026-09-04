#!/usr/bin/env python3
"""Repair reporting-only artifacts for the frozen BTC intraday validation.

The original validation trade ledgers are immutable inputs. This replay checks
their setup ids, outcomes and returns against the frozen engine, then repairs
two non-strategy artifacts: the configured ``2026P1`` slice label and matched
control exclusion around accepted inherited signals (not pre-cooldown
candidates). It does not select a parameter or read the repository holdout.
"""
from __future__ import annotations

import json
from typing import Any

import pandas as pd

from scripts.optimize_btcusdtp_k1k2_intraday_preholdout import (
    CONFIG_PATH,
    RESULTS,
    SELECTION_PATH,
    add_control_metrics,
    build_matched_controls,
    build_pair_universe,
    initial_params,
    load_config,
    load_featured,
    metric_row,
    run_arm,
    utc,
    write_csv,
    write_json,
)


def slice_label(stamp: pd.Timestamp) -> str:
    stamp = utc(stamp)
    if stamp.year == 2026:
        return "2026P1"
    return f"{stamp.year}H{1 if stamp.month <= 6 else 2}"


def corrected_slices(events: pd.DataFrame) -> pd.DataFrame:
    labels = events["entry_time"].map(slice_label)
    return pd.DataFrame(
        [
            {
                "fold": fold,
                **metric_row(events.loc[labels.eq(fold)].copy()),
            }
            for fold in ("2025H1", "2025H2", "2026P1")
        ]
    )


def assert_ledger_unchanged(original: pd.DataFrame, replay: pd.DataFrame) -> None:
    columns = ["setup_id", "outcome", "entry_price", "exit_price", "net_return"]
    pd.testing.assert_frame_equal(
        original[columns].reset_index(drop=True),
        replay[columns].reset_index(drop=True),
        check_dtype=False,
        check_exact=False,
        rtol=1e-12,
        atol=1e-12,
    )


def main() -> None:
    config = load_config()
    selection = json.loads(SELECTION_PATH.read_text(encoding="utf-8"))
    start = utc(config["window"]["validation_start_inclusive"])
    end = utc(config["window"]["validation_end_exclusive"])
    rows: list[dict[str, Any]] = []
    comparison: list[dict[str, Any]] = []
    for bar in ("15m", "5m"):
        frame, quality = load_featured(config, bar)
        universe = build_pair_universe(frame, config, bar)
        inherited = initial_params(config, bar)
        selected = selection["timeframes"][bar]["selected_params"]
        _, inherited_events = run_arm(
            universe, frame, config, bar, inherited, start, end
        )
        _, selected_events = run_arm(
            universe, frame, config, bar, selected, start, end
        )
        original = pd.read_csv(
            RESULTS / f"validation_{bar}_selected_trades.csv.gz",
            parse_dates=["entry_time", "exit_time"],
        )
        assert_ledger_unchanged(original, selected_events)
        controls, pairs = build_matched_controls(
            selected_events,
            frame,
            config,
            bar,
            start,
            end,
            set(inherited_events["k2_i"].astype(int)) if len(inherited_events) else set(),
        )
        metrics = add_control_metrics(metric_row(selected_events), pairs)
        rows.append({"bar": bar, "arm": "selected", **metrics})
        corrected = corrected_slices(selected_events)
        write_csv(corrected, RESULTS / f"validation_{bar}_selected_slices_corrected.csv")
        write_csv(controls, RESULTS / f"validation_{bar}_matched_controls_corrected.csv.gz")
        write_csv(pairs, RESULTS / f"validation_{bar}_matched_pairs_corrected.csv")
        original_pairs = pd.read_csv(RESULTS / f"validation_{bar}_matched_pairs.csv")
        original_matched = original_pairs[
            original_pairs["match_status"].eq("matched_exact")
        ]
        original_excess = float(original_matched["paired_excess_return"].mean() * 1e4)
        comparison.append(
            {
                "bar": bar,
                "trade_ledger_equal": True,
                "accepted_inherited_exclusion_signals": len(inherited_events),
                "original_candidate_exclusion_signals": int(
                    json.loads(
                        (RESULTS / f"validation_{bar}_receipt.json").read_text(
                            encoding="utf-8"
                        )
                    )["inherited_candidate_rows"]
                ),
                "original_matched_excess_bp": original_excess,
                "corrected_matched_excess_bp": metrics["matched_control_excess_bp"],
                "corrected_p_one_sided": metrics["paired_signflip_p_one_sided"],
                "holdout_rows_read": quality["holdout_rows_read"],
            }
        )
    write_csv(pd.DataFrame(rows), RESULTS / "validation_metrics_corrected.csv")
    write_csv(pd.DataFrame(comparison), RESULTS / "validation_artifact_correction.csv")
    write_json(
        RESULTS / "validation_artifact_correction_receipt.json",
        {
            "status": "reporting_and_control_artifacts_corrected",
            "frozen_trade_ledgers_changed": false,
            "parameters_changed": false,
            "validation_retuned": false,
            "holdout_rows_read": 0,
            "selection_receipt": str(SELECTION_PATH),
            "config": str(CONFIG_PATH),
            "corrections": [
                "map 2026-01/02 entries to the preregistered 2026P1 slice",
                "exclude controls around accepted inherited signals rather than pre-cooldown candidates",
            ],
            "comparison": comparison,
        },
    )
    print(pd.DataFrame(comparison).to_string(index=False))


if __name__ == "__main__":
    main()
