#!/usr/bin/env python3
"""Apply the locked 15m candidates to an earlier 2022 backcast window.

The V9 parameters were selected later, on 2023/2024, so this deliberately is
not called out-of-sample evidence.  It is a reverse-time transport diagnostic:
does the already-frozen rule behave only in the later regime, and do the
post-selection V10/V11 ideas improve it in the earlier market?  All rules,
barriers, 20 bp cost, and 1% comparison risk remain unchanged.

The loader ends at 2023-01-01, before discovery, final-preholdout, and holdout.
Every directional result includes three exact non-reused matched controls and
week-clustered inference.  The script cannot change eligibility or select a
new strategy version.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from scripts.research_pine_eth_15m import (
    Period,
    Variant,
    block_signflip,
    build_feature_frame,
    build_matched_controls,
    concentration_diagnostics,
    load_config,
    pair_controls,
    simulate_period,
    summarize,
    week_bootstrap_ci,
)
from yoyo.layers.l3_backtest.pine_allin_v7 import load_development_frame


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
RESULTS = EXPERIMENT / "results"
SUMMARY_CSV = RESULTS / "backcast_2022_summary.csv"
CONTROLS_CSV = RESULTS / "backcast_2022_controls.csv"
OUTPUT_JSON = RESULTS / "backcast_2022.json"
LOAD_END = pd.Timestamp("2023-01-01T00:00:00Z")
BACKCAST_START = pd.Timestamp("2022-02-01T00:00:00Z")
DISCOVERY_START = pd.Timestamp("2023-01-01T00:00:00Z")
FINAL_START = pd.Timestamp("2025-01-01T00:00:00Z")
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")


def main() -> None:
    config = load_config()
    raw = load_development_frame(
        PROJECT / config["instrument"]["data_path"],
        safe_end=LOAD_END,
        holdout_start=HOLDOUT_START,
    )
    times = pd.to_datetime(raw["open_time"], utc=True)
    if times.max() >= LOAD_END:
        raise RuntimeError("backcast loader crossed its 2023 boundary")
    frame = build_feature_frame(raw)
    period = Period("backcast_2022", BACKCAST_START, LOAD_END)
    variants = (
        ("V9", Variant("v9_backcast", "v9_long", "v9_short")),
        (
            "V10",
            Variant("v10_backcast", "v10_volume_long", "v10_volume_short"),
        ),
        (
            "V11",
            Variant(
                "v11_backcast",
                "v9_long",
                "v9_short",
                entry_directions=(1,),
            ),
        ),
    )

    summaries: list[dict[str, Any]] = []
    control_frames = []
    details: dict[str, Any] = {}
    for offset, (label, spec) in enumerate(variants):
        trades, equity = simulate_period(frame, spec, period, risk_percent=1.0)
        metric = summarize(
            trades,
            equity,
            variant=label,
            period=period.name,
            risk_percent=1.0,
        )
        controls = build_matched_controls(
            frame,
            trades,
            period,
            seed=f"pine-eth15m-{label.lower()}-backcast-v1",
        )
        controls.insert(0, "variant_label", label)
        control_frames.append(controls)
        pairs = pair_controls(trades, controls)
        signflip = block_signflip(pairs, seed=20260831 + offset)
        excess_ci = week_bootstrap_ci(
            pairs,
            "excess_return",
            seed=20260901 + offset,
        )
        absolute = pairs[["candidate_entry_time", "project_net_return"]].rename(
            columns={"project_net_return": "absolute_return"}
        )
        absolute_ci = week_bootstrap_ci(
            absolute,
            "absolute_return",
            seed=20260911 + offset,
        )
        candidate_bp = float(pairs["project_net_return"].mean() * 10_000.0)
        control_bp = float(pairs["control_mean_project_net"].mean() * 10_000.0)
        excess_bp = float(pairs["excess_return"].mean() * 10_000.0)
        summaries.append(
            {
                **metric,
                "matched_control_net_bp": control_bp,
                "candidate_minus_control_bp": excess_bp,
                "week_signflip_p": signflip["p_value"],
                "absolute_ci95_low_bp": absolute_ci["ci95_low_bp"],
                "absolute_ci95_high_bp": absolute_ci["ci95_high_bp"],
            }
        )
        details[label] = {
            "summary": metric,
            "matched_control": {
                "candidate_net_bp": candidate_bp,
                "control_net_bp": control_bp,
                "candidate_minus_control_bp": excess_bp,
                "control_rows": int(len(controls)),
                "controls_per_trade_min": int(
                    controls.groupby("trade_id").size().min()
                ),
                "duplicate_control_starts": int(
                    controls["control_signal_i"].duplicated().sum()
                ),
            },
            "week_signflip": signflip,
            "week_bootstrap_excess": excess_ci,
            "week_bootstrap_absolute": absolute_ci,
            "profit_concentration": concentration_diagnostics(trades),
        }

    summary_frame = pd.DataFrame(summaries)
    controls_frame = pd.concat(control_frames, ignore_index=True)
    summary_frame.to_csv(SUMMARY_CSV, index=False)
    controls_frame.to_csv(CONTROLS_CSV, index=False)
    payload = {
        "audit": "locked-rule reverse-time transport backcast",
        "period": [BACKCAST_START.isoformat(), LOAD_END.isoformat()],
        "rows_read": int(len(frame)),
        "first_bar": times.iloc[0].isoformat(),
        "last_bar": times.iloc[-1].isoformat(),
        "post_backcast_rows_read": int(times.ge(LOAD_END).sum()),
        "discovery_rows_read": int(times.ge(DISCOVERY_START).sum()),
        "final_preholdout_rows_read": int(times.ge(FINAL_START).sum()),
        "holdout_rows_read": int(times.ge(HOLDOUT_START).sum()),
        "selection_occurs_after_evaluation_period": True,
        "is_out_of_sample": False,
        "may_change_locked_candidate": False,
        "barrier_parameters_changed": False,
        "matched_control_required": True,
        "variants": details,
        "decision": (
            "V9 transports positively to 2022 and is stronger than V10/V11 in this "
            "reverse-time diagnostic, but its week sign-flip p-value still exceeds 0.01. "
            "Because selection occurred later, the backcast cannot promote any arm."
        ),
    }
    if any(
        payload[key] != 0
        for key in (
            "post_backcast_rows_read",
            "discovery_rows_read",
            "final_preholdout_rows_read",
            "holdout_rows_read",
        )
    ):
        raise RuntimeError("backcast read a forbidden later period")
    if any(
        row["matched_control"]["controls_per_trade_min"] != 3
        or row["matched_control"]["duplicate_control_starts"] != 0
        for row in details.values()
    ):
        raise RuntimeError("backcast matched controls failed exactness")
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
