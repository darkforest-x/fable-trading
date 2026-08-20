#!/usr/bin/env python3
"""Compare ETH 15m Pine results on local OKX swap and spot feeds.

This is a data-source sensitivity audit over their common, already-consumed
2025-07 through 2026-02 window.  Spot is not treated as a tradable substitute
for the perpetual; it is a nearby independent OHLC/volume feed used to measure
whether small feed changes alter signals or conclusions.  The exact same V9,
V10, and V11 rules, barriers, risk, and 20 bp cost are used.

Both parsers stop before 2026-03-01 and refuse repository holdout rows.  Results
remain post-selection diagnostics and cannot replace TradingView venue parity.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.research_pine_eth_15m import (
    Period,
    Variant,
    build_feature_frame,
    load_config,
    simulate_period,
    summarize,
)
from yoyo.layers.l3_backtest.pine_allin_v7 import load_development_frame


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
RESULTS = EXPERIMENT / "results"
CSV_OUTPUT = RESULTS / "feed_sensitivity.csv"
JSON_OUTPUT = RESULTS / "feed_sensitivity.json"
COMMON_START = pd.Timestamp("2025-07-01T00:00:00Z")
SAFE_END = pd.Timestamp("2026-03-01T00:00:00Z")
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
SPOT_PATH = PROJECT / "data/kline_fetched/okx_ETH_USDT_15m_41281.csv"


def load_feed(path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    raw = load_development_frame(
        path,
        safe_end=SAFE_END,
        holdout_start=HOLDOUT_START,
    )
    times = pd.to_datetime(raw["open_time"], utc=True)
    common = raw.loc[times.ge(COMMON_START)].copy().reset_index(drop=True)
    common_times = pd.to_datetime(common["open_time"], utc=True)
    quality = {
        "path": str(path.relative_to(PROJECT)),
        "rows_common": int(len(common)),
        "first_common_bar": common_times.iloc[0].isoformat(),
        "last_common_bar": common_times.iloc[-1].isoformat(),
        "holdout_rows_read": int(common_times.ge(HOLDOUT_START).sum()),
        "non_15m_gaps": int(
            common_times.diff().dropna().ne(pd.Timedelta(minutes=15)).sum()
        ),
    }
    if quality["holdout_rows_read"] or quality["non_15m_gaps"]:
        raise RuntimeError(f"feed quality failed: {quality}")
    return build_feature_frame(raw), quality


def jaccard(left: set[pd.Timestamp], right: set[pd.Timestamp]) -> dict[str, Any]:
    intersection = left & right
    union = left | right
    return {
        "left": len(left),
        "right": len(right),
        "intersection": len(intersection),
        "union": len(union),
        "jaccard": float(len(intersection) / len(union)) if union else 1.0,
    }


def main() -> None:
    config = load_config()
    swap_path = PROJECT / config["instrument"]["data_path"]
    swap, swap_quality = load_feed(swap_path)
    spot, spot_quality = load_feed(SPOT_PATH)
    period = Period("common_2025_07_to_2026_03", COMMON_START, SAFE_END)
    common_times = pd.date_range(
        COMMON_START,
        SAFE_END - pd.Timedelta(minutes=15),
        freq="15min",
        tz="UTC",
    )
    raw_common: dict[str, pd.DataFrame] = {}
    for name, frame in (("swap", swap), ("spot", spot)):
        selected = frame.loc[
            pd.to_datetime(frame["open_time"], utc=True).ge(COMMON_START)
            & pd.to_datetime(frame["open_time"], utc=True).lt(SAFE_END)
        ].copy()
        selected = selected.set_index(pd.to_datetime(selected["open_time"], utc=True)).sort_index()
        if not selected.index.equals(common_times):
            raise RuntimeError(f"{name} common timeline does not exactly cover the audit window")
        raw_common[name] = selected

    joined = raw_common["swap"][["open", "high", "low", "close"]].join(
        raw_common["spot"][["open", "high", "low", "close"]],
        lsuffix="_swap",
        rsuffix="_spot",
    )
    close_basis = (joined["close_spot"] / joined["close_swap"] - 1.0) * 10_000.0
    volume_feature_corr = float(
        raw_common["swap"]["vol_ratio_mean8"].corr(raw_common["spot"]["vol_ratio_mean8"])
    )

    variants = (
        ("V9", Variant("v9", "v9_long", "v9_short")),
        ("V10", Variant("v10", "v10_volume_long", "v10_volume_short")),
        (
            "V11",
            Variant("v11", "v9_long", "v9_short", entry_directions=(1,)),
        ),
    )
    result_rows = []
    entry_comparisons: dict[str, Any] = {}
    ledgers: dict[tuple[str, str], pd.DataFrame] = {}
    for variant_label, spec in variants:
        for feed_name, frame in (("swap", swap), ("spot", spot)):
            feed_spec = Variant(
                f"{spec.name}_{feed_name}",
                spec.long_column,
                spec.short_column,
                entry_directions=spec.entry_directions,
            )
            trades, equity = simulate_period(frame, feed_spec, period)
            ledgers[(variant_label, feed_name)] = trades
            summary = summarize(
                trades,
                equity,
                variant=variant_label,
                period=feed_name,
                risk_percent=1.0,
            )
            result_rows.append({"variant": variant_label, "feed": feed_name, **summary})
        swap_trades = ledgers[(variant_label, "swap")].copy()
        spot_trades = ledgers[(variant_label, "spot")].copy()
        swap_entries = set(pd.to_datetime(swap_trades["entry_time"], utc=True))
        spot_entries = set(pd.to_datetime(spot_trades["entry_time"], utc=True))
        overlap = swap_trades.merge(
            spot_trades,
            on="entry_time",
            how="inner",
            suffixes=("_swap", "_spot"),
        )
        entry_comparisons[variant_label] = {
            **jaccard(swap_entries, spot_entries),
            "same_direction_on_common_entries": int(
                overlap["direction_swap"].eq(overlap["direction_spot"]).sum()
            ),
            "same_exit_time_on_common_entries": int(
                pd.to_datetime(overlap["exit_time_swap"], utc=True)
                .eq(pd.to_datetime(overlap["exit_time_spot"], utc=True))
                .sum()
            ),
            "mean_absolute_net_return_delta_bp": float(
                (overlap["project_net_return_swap"] - overlap["project_net_return_spot"])
                .abs()
                .mean()
                * 10_000.0
            ),
        }

    raw_signal_comparisons = {}
    for signal in ("v9_long", "v9_short", "v10_volume_long", "v10_volume_short"):
        sets = {
            name: set(frame.index[frame[signal].fillna(False)])
            for name, frame in raw_common.items()
        }
        raw_signal_comparisons[signal] = jaccard(sets["swap"], sets["spot"])

    table = pd.DataFrame(result_rows)
    table.to_csv(CSV_OUTPUT, index=False)
    payload = {
        "audit": "OKX swap versus spot 15m feed sensitivity",
        "status": "consumed-final proxy diagnostic only",
        "period": [COMMON_START.isoformat(), SAFE_END.isoformat()],
        "swap_quality": swap_quality,
        "spot_quality": spot_quality,
        "common_bars": int(len(common_times)),
        "close_spot_minus_swap_basis_bp": {
            "median": float(close_basis.median()),
            "q05": float(close_basis.quantile(0.05)),
            "q95": float(close_basis.quantile(0.95)),
            "maximum_absolute": float(close_basis.abs().max()),
        },
        "vol_ratio_mean8_cross_feed_correlation": volume_feature_corr,
        "raw_signal_comparisons": raw_signal_comparisons,
        "executed_entry_comparisons": entry_comparisons,
        "holdout_rows_read": int(
            swap_quality["holdout_rows_read"] + spot_quality["holdout_rows_read"]
        ),
        "tradingview_parity_passed": False,
        "spot_is_perpetual_substitute": False,
        "interpretation": (
            "V9/V11 price-based entries are highly stable across nearby OKX feeds in the "
            "common window. V10 is more feed-sensitive because spot and swap volume are "
            "different markets. This supports V9 as the canonical proxy baseline but does "
            "not identify the user's TradingView venue or prove parity."
        ),
    }
    if payload["holdout_rows_read"] != 0:
        raise RuntimeError("feed sensitivity must not read repository holdout")
    if not np.isfinite(volume_feature_corr):
        raise RuntimeError("volume feature correlation is not finite")
    JSON_OUTPUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
