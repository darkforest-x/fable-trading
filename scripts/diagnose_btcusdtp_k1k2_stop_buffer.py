#!/usr/bin/env python3
"""Decompose the frozen K2 stop-buffer development result.

Signal identity and the baseline ledger come from the completed development
selection. For the fixed-cohort counterfactual, the script reads ATR14 at K2
and future OHLC only through the already-registered 12-hour outcome window.
It never selects a parameter and never reads the repository holdout.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.optimize_btcusdtp_k1k2_intraday_preholdout import metric_row, resolve_exit
from scripts.research_btcusdtp_k1k2_stop_buffer import (
    CONFIG_PATH,
    RESULTS,
    build_core_pairs,
    filter_candidates,
    load_base_frame,
    load_config,
    period_candidates,
    run_arm,
    signal_params,
    utc,
    with_reference_features,
    write_csv,
    write_json,
)
from scripts.research_two_key_candle_ma_retest_1h import sha256_file

PROJECT = Path(__file__).resolve().parents[1]


def signal_key(frame: pd.DataFrame) -> pd.Series:
    return pd.Series(
        list(
            zip(
                frame["direction"].astype(int),
                frame["k1_i"].astype(int),
                frame["k2_i"].astype(int),
            )
        ),
        index=frame.index,
    )


def fixed_cohort_outcomes(
    baseline: pd.DataFrame,
    frame: pd.DataFrame,
    config: dict[str, Any],
    bar: str,
    buffer_atr: float,
) -> pd.DataFrame:
    """Reprice only the stop/3R target for the exact baseline entries."""

    outcomes: list[dict[str, Any]] = []
    for event in baseline.to_dict("records"):
        k2_i = int(event["k2_i"])
        direction = int(event["direction"])
        atr = float(frame.loc[k2_i, "atr"])
        stop = float(event["k2_extreme_price"]) - direction * buffer_atr * atr
        risk = direction * (float(event["entry_price"]) - stop)
        repriced = {
            **event,
            "stop_price": stop,
            "risk_price": risk,
            "risk_fraction": risk / float(event["entry_price"]),
            "target_price": float(event["entry_price"])
            + direction * risk * float(config["execution_frozen"]["target_r"]),
        }
        outcomes.append(resolve_exit(frame, repriced, config, bar))
    return pd.DataFrame(outcomes)


def main() -> None:
    config = load_config()
    start = utc(config["window"]["development_start_inclusive"])
    end = utc(config["window"]["development_end_exclusive"])
    fixed_rows: list[dict[str, Any]] = []
    cohort_rows: list[dict[str, Any]] = []
    gate_rows: list[dict[str, Any]] = []
    transition_rows: list[dict[str, Any]] = []

    for bar in ("15m", "5m"):
        base, _ = load_base_frame(config, bar)
        params = signal_params(config, bar)
        frame = with_reference_features(base, int(params["ma_period"]))
        universe = build_core_pairs(
            frame,
            ma_period=int(params["ma_period"]),
            maximum_gap_bars=int(
                config["signal_frozen"][bar]["maximum_pair_gap_bars"]
            ),
        )
        candidates = period_candidates(
            filter_candidates(universe, params), frame, config, bar, start, end
        )
        baseline_path = RESULTS / f"development_{bar}_selected_trades.csv.gz"
        baseline = pd.read_csv(baseline_path)
        baseline["key"] = signal_key(baseline)
        baseline_keys = set(baseline["key"])

        for raw_buffer in config["factor"]["grid"]:
            buffer_atr = float(raw_buffer)
            decisions, events = run_arm(
                candidates, frame, config, bar, params, buffer_atr
            )
            events = events.copy()
            events["key"] = signal_key(events)
            overlap = events[events["key"].isin(baseline_keys)]
            added = events[~events["key"].isin(baseline_keys)]
            cohort_rows.append(
                {
                    "bar": bar,
                    "k2_stop_buffer_atr": buffer_atr,
                    "events": len(events),
                    "overlap_with_baseline": len(overlap),
                    "baseline_events_displaced": len(baseline) - len(overlap),
                    "newly_accepted_events": len(added),
                    "all_mean_gross_bp": float(events["gross_return"].mean() * 1e4),
                    "overlap_mean_gross_bp": float(
                        overlap["gross_return"].mean() * 1e4
                    ),
                    "new_mean_gross_bp": float(added["gross_return"].mean() * 1e4)
                    if len(added)
                    else np.nan,
                    "median_stop_distance_atr": float(
                        events["stop_distance_atr"].median()
                    ),
                    "median_fee_to_risk": float(events["fee_to_risk"].median()),
                }
            )
            counts = Counter(decisions["decision"].astype(str))
            gate_rows.extend(
                {
                    "bar": bar,
                    "k2_stop_buffer_atr": buffer_atr,
                    "decision": decision,
                    "events": int(count),
                }
                for decision, count in sorted(counts.items())
            )

            repriced = fixed_cohort_outcomes(
                baseline, frame, config, bar, buffer_atr
            )
            if np.isclose(buffer_atr, 0.0):
                if not np.allclose(
                    repriced["gross_return"].to_numpy(dtype=float),
                    baseline["gross_return"].to_numpy(dtype=float),
                    atol=1e-12,
                    rtol=0.0,
                ):
                    raise AssertionError(f"{bar} zero-buffer fixed cohort drift")
                if repriced["outcome"].astype(str).tolist() != baseline[
                    "outcome"
                ].astype(str).tolist():
                    raise AssertionError(f"{bar} zero-buffer outcome drift")
            fixed_rows.append(
                {
                    "bar": bar,
                    "k2_stop_buffer_atr": buffer_atr,
                    **metric_row(repriced),
                }
            )
            transitions = Counter(
                zip(
                    baseline["outcome"].astype(str),
                    repriced["outcome"].astype(str),
                )
            )
            transition_rows.extend(
                {
                    "bar": bar,
                    "k2_stop_buffer_atr": buffer_atr,
                    "baseline_outcome": before,
                    "buffered_outcome": after,
                    "events": int(count),
                }
                for (before, after), count in sorted(transitions.items())
            )

    write_csv(pd.DataFrame(fixed_rows), RESULTS / "diagnostic_fixed_cohort.csv")
    write_csv(pd.DataFrame(cohort_rows), RESULTS / "diagnostic_dynamic_cohort.csv")
    write_csv(pd.DataFrame(gate_rows), RESULTS / "diagnostic_gate_counts.csv")
    write_csv(
        pd.DataFrame(transition_rows), RESULTS / "diagnostic_outcome_transitions.csv"
    )
    write_json(
        RESULTS / "diagnostic_receipt.json",
        {
            "status": "post_selection_descriptive_only",
            "selection_fields_read": [],
            "config_sha256": sha256_file(CONFIG_PATH),
            "selection_receipt_sha256": sha256_file(RESULTS / "selection_receipt.json"),
            "holdout_rows_read": 0,
            "outputs": [
                "diagnostic_fixed_cohort.csv",
                "diagnostic_dynamic_cohort.csv",
                "diagnostic_gate_counts.csv",
                "diagnostic_outcome_transitions.csv",
            ],
        },
    )


if __name__ == "__main__":
    main()
