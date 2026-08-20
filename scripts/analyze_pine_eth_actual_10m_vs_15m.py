#!/usr/bin/env python3
"""Compare actual 10m and 15m ETH strategy behavior on a common short window.

Local OKX 5m bars are aggregated in exact pairs to produce real 10m OHLCV;
this is not a wall-clock parameter approximation.  The available 5m prefix is
short (2025-12-20 onward), so evaluation starts on 2025-12-23 after indicator
warmup and ends before 2026-03-01.  Four frozen arms separate timeframe from
rule change: V8 on 10m/15m and V9 on 10m/15m.  Barriers, cost, and risk are
identical.  Each directional result attempts the frozen exact matched-control
design; an underfilled causal stratum is reported as unavailable rather than
being backfilled from the future or weakened after seeing the result.

This consumed-final, post-selection diagnostic cannot select or promote an
arm and cannot establish TradingView venue parity.  Columns used are causal
OHLCV through each signal bar; only recorded exits inspect later bars.
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
    load_config,
    pair_controls,
    simulate_period,
    summarize,
)
from yoyo.layers.l3_backtest.pine_allin_v7 import load_development_frame


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
RESULTS = EXPERIMENT / "results"
SUMMARY_CSV = RESULTS / "actual_10m_vs_15m.csv"
CONTROLS_CSV = RESULTS / "actual_10m_vs_15m_controls.csv"
OUTPUT_JSON = RESULTS / "actual_10m_vs_15m.json"
FIVE_MINUTE_PATH = PROJECT / "data/kline_fetched/okx_ETH_USDT_SWAP_5m_57699.csv"
EVALUATION_START = pd.Timestamp("2025-12-23T00:00:00Z")
SAFE_END = pd.Timestamp("2026-03-01T00:00:00Z")
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")


def aggregate_exact_10m(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    times = pd.to_datetime(frame["open_time"], utc=True)
    five_minute_gaps = int(times.diff().dropna().ne(pd.Timedelta(minutes=5)).sum())
    grouped = (
        frame.set_index("open_time")
        .resample("10min", origin="epoch", label="left", closed="left")
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
            child_bars=("close", "size"),
        )
        .dropna()
        .reset_index()
    )
    incomplete = int(grouped["child_bars"].ne(2).sum())
    if five_minute_gaps or incomplete:
        raise RuntimeError(
            f"cannot form exact 10m bars: 5m_gaps={five_minute_gaps}, incomplete={incomplete}"
        )
    out = grouped.drop(columns="child_bars")
    out_times = pd.to_datetime(out["open_time"], utc=True)
    quality = {
        "source_5m_rows": int(len(frame)),
        "source_5m_first": times.iloc[0].isoformat(),
        "source_5m_last": times.iloc[-1].isoformat(),
        "source_5m_gaps": five_minute_gaps,
        "aggregated_10m_rows": int(len(out)),
        "aggregated_10m_first": out_times.iloc[0].isoformat(),
        "aggregated_10m_last": out_times.iloc[-1].isoformat(),
        "parents_not_exactly_two_5m_bars": incomplete,
        "aggregated_10m_gaps": int(
            out_times.diff().dropna().ne(pd.Timedelta(minutes=10)).sum()
        ),
    }
    return out, quality


def main() -> None:
    config = load_config()
    raw5 = load_development_frame(
        FIVE_MINUTE_PATH,
        safe_end=SAFE_END,
        holdout_start=HOLDOUT_START,
        chunksize=1_000,
    )
    raw10, ten_quality = aggregate_exact_10m(raw5)
    raw15 = load_development_frame(
        PROJECT / config["instrument"]["data_path"],
        safe_end=SAFE_END,
        holdout_start=HOLDOUT_START,
    )
    frame_by_minutes = {
        10: build_feature_frame(raw10),
        15: build_feature_frame(raw15),
    }
    period = Period("common_short_20251223_202602", EVALUATION_START, SAFE_END)
    specifications = (
        ("V8_10m", 10, Variant("v8_actual_10m", "v8_long", "v8_short")),
        ("V8_15m", 15, Variant("v8_actual_15m", "v8_long", "v8_short")),
        ("V9_10m", 10, Variant("v9_actual_10m", "v9_long", "v9_short")),
        ("V9_15m", 15, Variant("v9_actual_15m", "v9_long", "v9_short")),
    )
    summaries = []
    controls_all = []
    details: dict[str, Any] = {}
    for offset, (label, minutes, spec) in enumerate(specifications):
        frame = frame_by_minutes[minutes]
        trades, equity = simulate_period(frame, spec, period, risk_percent=1.0)
        metric = summarize(
            trades,
            equity,
            variant=label,
            period=period.name,
            risk_percent=1.0,
        )
        control_failure: str | None = None
        try:
            controls = build_matched_controls(
                frame,
                trades,
                period,
                seed=f"pine-eth-actual-timeframe-{label.lower()}-v1",
            )
        except RuntimeError as exc:
            # Exact same-month / HK-session / lagged-ATR-quintile strata are
            # part of the preregistered null. Sparse short-window strata stay
            # missing; broadening them would be outcome-aware control redesign.
            control_failure = str(exc)
            controls = pd.DataFrame()
            control_bp = None
            excess_bp = None
            signflip: dict[str, Any] = {
                "available": False,
                "p_value": None,
                "failure_reason": control_failure,
            }
        else:
            controls.insert(0, "variant_label", label)
            controls_all.append(controls)
            pairs = pair_controls(trades, controls)
            signflip = {
                "available": True,
                **block_signflip(pairs, seed=20260920 + offset),
                "failure_reason": None,
            }
            control_bp = float(pairs["control_mean_project_net"].mean() * 10_000.0)
            excess_bp = float(pairs["excess_return"].mean() * 10_000.0)
        row = {
            "label": label,
            "bar_minutes": minutes,
            **metric,
            "matched_control_net_bp": control_bp,
            "candidate_minus_control_bp": excess_bp,
            "week_signflip_p": signflip["p_value"],
        }
        summaries.append(row)
        details[label] = {
            "bar_minutes": minutes,
            "summary": metric,
            "matched_control": {
                "available": control_failure is None,
                "failure_reason": control_failure,
                "control_rows": int(len(controls)),
                "controls_per_trade_min": (
                    int(controls.groupby("trade_id").size().min())
                    if control_failure is None
                    else None
                ),
                "duplicate_control_starts": (
                    int(controls["control_signal_i"].duplicated().sum())
                    if control_failure is None
                    else None
                ),
                "control_net_bp": control_bp,
                "candidate_minus_control_bp": excess_bp,
            },
            "week_signflip": signflip,
        }

    table = pd.DataFrame(summaries)
    controls_table = (
        pd.concat(controls_all, ignore_index=True)
        if controls_all
        else pd.DataFrame(columns=["variant_label"])
    )
    table.to_csv(SUMMARY_CSV, index=False)
    controls_table.to_csv(CONTROLS_CSV, index=False)

    net = table.set_index("label")["project_net_bp_per_trade"]
    all_times = pd.concat(
        [
            pd.to_datetime(raw5["open_time"], utc=True),
            pd.to_datetime(raw15["open_time"], utc=True),
        ],
        ignore_index=True,
    )
    payload = {
        "audit": "actual OKX ETH 10m versus 15m common-window comparison",
        "period": [EVALUATION_START.isoformat(), SAFE_END.isoformat()],
        "status": "short consumed-final post-selection diagnostic only",
        "ten_minute_quality": ten_quality,
        "holdout_rows_read": int(all_times.ge(HOLDOUT_START).sum()),
        "post_safe_rows_read": int(all_times.ge(SAFE_END).sum()),
        "tradingview_parity_passed": False,
        "barrier_parameters_changed": False,
        "selection_or_promotion_allowed": False,
        "matched_control_unavailable_variants": [
            label
            for label, row in details.items()
            if not row["matched_control"]["available"]
        ],
        "variants": details,
        "isolated_deltas_bp_per_trade": {
            "V8_15m_minus_V8_10m": float(net["V8_15m"] - net["V8_10m"]),
            "V9_15m_minus_V9_10m": float(net["V9_15m"] - net["V9_10m"]),
            "V9_minus_V8_within_10m": float(net["V9_10m"] - net["V8_10m"]),
            "V9_minus_V8_within_15m": float(net["V9_15m"] - net["V8_15m"]),
        },
        "decision": (
            "In this roughly ten-week common window, actual 10m V8 beats 15m V8 and "
            "both V9 arms are negative after cost. Exact causal matched controls are "
            "reported only where all frozen strata have three unique starts; underfilled "
            "arms have no control estimate and this diagnostic remains descriptive. "
            "The short post-selection window cannot overturn the long chronological V9 "
            "freeze, but it rejects any claim that 15m is inherently superior to 10m."
        ),
    }
    if payload["holdout_rows_read"] or payload["post_safe_rows_read"]:
        raise RuntimeError("actual timeframe audit crossed a data boundary")
    if any(
        row["matched_control"]["available"]
        and (
            row["matched_control"]["controls_per_trade_min"] != 3
            or row["matched_control"]["duplicate_control_starts"] != 0
        )
        for row in details.values()
    ):
        raise RuntimeError("actual timeframe controls failed exactness")
    if any(
        not row["matched_control"]["available"]
        and not row["matched_control"]["failure_reason"]
        for row in details.values()
    ):
        raise RuntimeError("unavailable actual timeframe control lacks a failure reason")
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
