#!/usr/bin/env python3
"""Quantify static-filter bias for state-changing Pine entry gates.

The audit compares two tempting but different operations: filtering an already
executed V9 trade CSV, versus replaying the volume or side gate inside the
position/reversal/cooldown state machine.  It fits no model and changes no
rule.  Data end before 2026-03-01; final results are consumed diagnostics.

This is direct evidence for the L2 integration contract: an LR/LightGBM score
must be evaluated at signal time inside dynamic replay, never by filtering a
baseline ledger after the fact.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from scripts.research_pine_eth_15m import build_feature_frame, load_config
from yoyo.layers.l3_backtest.pine_allin_v7 import load_development_frame


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
RESULTS = EXPERIMENT / "results"
OUTPUT_CSV = RESULTS / "stateful_gate_static_vs_dynamic.csv"
OUTPUT_JSON = RESULTS / "stateful_gate_static_vs_dynamic.json"
SAFE_END = pd.Timestamp("2026-03-01T00:00:00Z")
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")


def compare_ledgers(
    static: pd.DataFrame,
    dynamic: pd.DataFrame,
    *,
    gate: str,
    split: str,
) -> dict[str, Any]:
    static_entries = set(static["entry_i"].astype(int))
    dynamic_entries = set(dynamic["entry_i"].astype(int))
    intersection = static_entries & dynamic_entries
    union = static_entries | dynamic_entries
    common = static.merge(
        dynamic,
        on="entry_i",
        how="inner",
        suffixes=("_static", "_dynamic"),
    )
    return {
        "gate": gate,
        "split": split,
        "static_trades": int(len(static)),
        "dynamic_trades": int(len(dynamic)),
        "entry_intersection": int(len(intersection)),
        "entry_union": int(len(union)),
        "entry_jaccard": float(len(intersection) / len(union)) if union else 1.0,
        "common_entry_same_exit": int(
            common["exit_i_static"].eq(common["exit_i_dynamic"]).sum()
        ),
        "static_net_bp_per_trade": float(
            static["project_net_return"].mean() * 10_000.0
        ),
        "dynamic_net_bp_per_trade": float(
            dynamic["project_net_return"].mean() * 10_000.0
        ),
        "static_minus_dynamic_net_bp": float(
            (static["project_net_return"].mean() - dynamic["project_net_return"].mean())
            * 10_000.0
        ),
    }


def main() -> None:
    config = load_config()
    raw = load_development_frame(
        PROJECT / config["instrument"]["data_path"],
        safe_end=SAFE_END,
        holdout_start=HOLDOUT_START,
    )
    frame = build_feature_frame(raw)
    times = pd.to_datetime(frame["open_time"], utc=True)
    all_trades = pd.read_csv(RESULTS / "trades.csv")
    v11_all = pd.read_csv(RESULTS / "v11_long_only_trades.csv")
    rows = []
    for split in (
        "discovery_2023",
        "confirmation_2024",
        "final_preholdout_2025_202602",
    ):
        v9 = all_trades.loc[
            all_trades["variant"].eq("v9_locked") & all_trades["split"].eq(split)
        ].copy()
        v10 = all_trades.loc[
            all_trades["variant"].eq("v10_volume_hypothesis")
            & all_trades["split"].eq(split)
        ].copy()
        volume_gate = (
            frame["vol_ratio_mean8"]
            .iloc[v9["signal_i"].astype(int)]
            .ge(1.0)
            .to_numpy()
        )
        static_v10 = v9.loc[volume_gate].copy()
        rows.append(
            compare_ledgers(
                static_v10,
                v10,
                gate="vol_ratio_mean8_ge1",
                split=split,
            )
        )

        static_long = v9.loc[v9["direction"].eq("long")].copy()
        dynamic_long = v11_all.loc[v11_all["split"].eq(split)].copy()
        rows.append(
            compare_ledgers(
                static_long,
                dynamic_long,
                gate="long_only",
                split=split,
            )
        )

    table = pd.DataFrame(rows)
    table.to_csv(OUTPUT_CSV, index=False)
    final = table.loc[table["split"].eq("final_preholdout_2025_202602")]
    payload = {
        "audit": "static ledger filtering versus dynamic stateful replay",
        "holdout_rows_read": int(times.ge(HOLDOUT_START).sum()),
        "training_or_scoring_performed": False,
        "barrier_parameters_changed": False,
        "final_preholdout_already_consumed": True,
        "comparisons": rows,
        "final_summary": {
            row.gate: {
                "entry_jaccard": row.entry_jaccard,
                "static_net_bp_per_trade": row.static_net_bp_per_trade,
                "dynamic_net_bp_per_trade": row.dynamic_net_bp_per_trade,
                "static_minus_dynamic_net_bp": row.static_minus_dynamic_net_bp,
            }
            for row in final.itertuples(index=False)
        },
        "decision": (
            "Both audited gates change subsequent entry availability and/or cooldown state. "
            "Static filtering produces a different ledger and overstates final net expectancy. "
            "Any future judgment score must execute inside dynamic replay."
        ),
        "static_top_decile_filtering_valid_for_l2": False,
    }
    if payload["holdout_rows_read"]:
        raise RuntimeError("stateful gate audit reached holdout")
    if not final["static_minus_dynamic_net_bp"].gt(0.0).all():
        raise RuntimeError("expected static-filter optimism is no longer visible")
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
