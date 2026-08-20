#!/usr/bin/env python3
"""Audit the frozen ETH 15m V9 exits against causal 3-minute paths.

Inputs are the canonical 15-minute V9 trade ledger plus OKX 3-minute bars.
Only bars before ``2026-03-01T00:00:00Z`` are parsed.  The 3-minute bars are
used solely to refine the path inside each 15-minute bar; signals, ATR, stop
distance, break-even thresholds, costs, and split boundaries remain frozen.

No feature in this audit sees beyond its completed 15-minute bar.  Initial and
already-armed stops are checked through the five 3-minute sub-bars in time
order.  A break-even trigger observed during a 15-minute bar becomes active
only after that bar closes, matching the confirmed-bar Python/Pine contract.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from yoyo.layers.l3_backtest.pine_allin_v7 import load_development_frame


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
RESULTS = EXPERIMENT / "results"
CONFIG_PATH = EXPERIMENT / "config.json"
TRADES_PATH = RESULTS / "trades.csv"
THREE_MINUTE_PATH = PROJECT / "data/kline_deep/okx_ETH_USDT_SWAP_3m_525599.csv"
OUTPUT_JSON = RESULTS / "intrabar_3m_reconciliation.json"
OUTPUT_CSV = RESULTS / "intrabar_3m_trades.csv"


def _utc(value: str | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    return timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")


def _bounded_frame_digest(frame: pd.DataFrame) -> str:
    """Hash only the parsed pre-holdout frame, never the full source file."""

    columns = ["open_time", "open", "high", "low", "close", "volume"]
    canonical = frame.loc[:, columns].copy()
    canonical["open_time"] = pd.to_datetime(canonical["open_time"], utc=True).map(
        lambda value: value.isoformat()
    )
    payload = canonical.to_csv(index=False, lineterminator="\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_three_minute_prefix(config: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load and validate the bounded 3-minute prefix used by this audit."""

    safe_end = _utc(config["time_contract"]["safe_end_exclusive"])
    holdout_start = _utc(config["time_contract"]["holdout_start"])
    frame = load_development_frame(
        THREE_MINUTE_PATH,
        safe_end=safe_end,
        holdout_start=holdout_start,
    )
    times = pd.to_datetime(frame["open_time"], utc=True)
    final_start = _utc(config["time_contract"]["final_preholdout_start"])
    frame = frame.loc[times.ge(final_start)].copy().reset_index(drop=True)
    times = pd.to_datetime(frame["open_time"], utc=True)
    numeric = frame[["open", "high", "low", "close", "volume"]].apply(
        pd.to_numeric, errors="coerce"
    )
    deltas = times.diff().dropna()
    quality = {
        "source_path": str(THREE_MINUTE_PATH.relative_to(PROJECT)),
        "bounded_prefix_sha256": _bounded_frame_digest(frame),
        "full_file_hash_intentionally_omitted": True,
        "rows": int(len(frame)),
        "first_bar": times.iloc[0].isoformat(),
        "last_bar": times.iloc[-1].isoformat(),
        "safe_end_exclusive": safe_end.isoformat(),
        "holdout_start": holdout_start.isoformat(),
        "holdout_rows_read": int(times.ge(holdout_start).sum()),
        "duplicate_timestamps": int(times.duplicated().sum()),
        "non_3m_gaps": int(deltas.ne(pd.Timedelta(minutes=3)).sum()),
        "null_ohlcv_cells": int(numeric.isna().sum().sum()),
        "ohlc_body_violations": int(
            numeric["high"].lt(numeric[["open", "close"]].max(axis=1)).sum()
            + numeric["low"].gt(numeric[["open", "close"]].min(axis=1)).sum()
        ),
    }
    refused = (
        "holdout_rows_read",
        "duplicate_timestamps",
        "non_3m_gaps",
        "null_ohlcv_cells",
        "ohlc_body_violations",
    )
    if any(quality[key] for key in refused):
        raise RuntimeError(f"3-minute data quality contract failed: {quality}")
    frame[numeric.columns] = numeric
    frame["open_time"] = times
    return frame, quality


def aggregate_three_to_fifteen(frame: pd.DataFrame) -> pd.DataFrame:
    """Aggregate five causal 3-minute bars into their 15-minute parent."""

    indexed = frame.set_index("open_time").sort_index()
    aggregated = indexed.resample("15min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    counts = indexed["close"].resample("15min", label="left", closed="left").count()
    aggregated["subbar_count"] = counts
    return aggregated.dropna(subset=["open", "high", "low", "close"])


def compare_parent_bars(
    three_minute: pd.DataFrame,
    *,
    safe_end: pd.Timestamp,
    holdout_start: pd.Timestamp,
) -> dict[str, Any]:
    """Prove that 3-minute OHLC reconstructs the canonical 15-minute feed."""

    fifteen_path = PROJECT / "data/kline_deep/okx_ETH_USDT_SWAP_15m_158499.csv"
    fifteen = load_development_frame(
        fifteen_path,
        safe_end=safe_end,
        holdout_start=holdout_start,
    )
    fifteen["open_time"] = pd.to_datetime(fifteen["open_time"], utc=True)
    start = three_minute["open_time"].iloc[0]
    fifteen = fifteen.loc[fifteen["open_time"].ge(start)].set_index("open_time").sort_index()
    aggregate = aggregate_three_to_fifteen(three_minute)
    joined = fifteen.join(aggregate, how="inner", lsuffix="_15m", rsuffix="_3m")
    columns = ("open", "high", "low", "close")
    maximum_error = {
        column: float((joined[f"{column}_15m"] - joined[f"{column}_3m"]).abs().max())
        for column in columns
    }
    mismatch_count = {
        column: int(
            (joined[f"{column}_15m"] - joined[f"{column}_3m"]).abs().gt(1e-9).sum()
        )
        for column in columns
    }
    result = {
        "expected_15m_bars": int(len(fifteen)),
        "joined_15m_bars": int(len(joined)),
        "parents_with_exactly_five_subbars": int(joined["subbar_count"].eq(5).sum()),
        "maximum_absolute_ohlc_error": maximum_error,
        "ohlc_mismatch_count": mismatch_count,
    }
    if result["joined_15m_bars"] != result["expected_15m_bars"]:
        raise RuntimeError(f"3m/15m parent coverage mismatch: {result}")
    if result["parents_with_exactly_five_subbars"] != result["joined_15m_bars"]:
        raise RuntimeError(f"not every 15m parent has five 3m bars: {result}")
    if any(mismatch_count.values()):
        raise RuntimeError(f"3m bars do not reconstruct 15m OHLC: {result}")
    return result


def replay_trade(
    trade: pd.Series,
    *,
    subbars: pd.DataFrame,
    parents: pd.DataFrame,
    break_even_trigger_percent: float = 1.5,
    break_even_offset_percent: float = 0.1,
) -> dict[str, Any]:
    """Replay one fixed V9 trade through ordered 3-minute sub-bars."""

    direction = 1 if trade["direction"] == "long" else -1
    entry_time = _utc(trade["entry_time"])
    canonical_exit_time = _utc(trade["exit_time"])
    entry_price = float(trade["entry_price"])
    stop_price = float(trade["initial_stop_price"])
    exit_reason = str(trade["exit_reason"])
    relevant = subbars.loc[
        subbars["open_time"].ge(entry_time)
        & subbars["open_time"].lt(canonical_exit_time + pd.Timedelta(minutes=15))
    ]
    replay_exit_time: pd.Timestamp | None = None
    replay_exit_price: float | None = None
    replay_reason: str | None = None
    trigger_subbar: pd.Timestamp | None = None
    break_even_armed_at: pd.Timestamp | None = None
    break_even_arm_count = 0

    for parent_time, parent_subbars in relevant.groupby(
        relevant["open_time"].dt.floor("15min"), sort=True
    ):
        parent_time = _utc(parent_time)
        if parent_time < entry_time.floor("15min"):
            continue

        # Reversals fill at the 15-minute open before that bar's price path.
        if exit_reason == "reverse" and parent_time == canonical_exit_time:
            replay_exit_time = parent_time
            replay_exit_price = float(parent_subbars.iloc[0]["open"])
            replay_reason = "reverse"
            break

        for row in parent_subbars.itertuples(index=False):
            if direction > 0 and float(row.low) <= stop_price:
                replay_exit_time = _utc(row.open_time)
                replay_exit_price = min(float(row.open), stop_price)
                replay_reason = "stop"
                trigger_subbar = replay_exit_time
                break
            if direction < 0 and float(row.high) >= stop_price:
                replay_exit_time = _utc(row.open_time)
                replay_exit_price = max(float(row.open), stop_price)
                replay_reason = "stop"
                trigger_subbar = replay_exit_time
                break
        if replay_exit_time is not None:
            break

        parent = parents.loc[parent_time]
        if direction > 0 and float(parent["high"]) >= entry_price * (
            1.0 + break_even_trigger_percent / 100.0
        ):
            updated = max(stop_price, entry_price * (1.0 + break_even_offset_percent / 100.0))
            if updated > stop_price:
                stop_price = updated
                break_even_armed_at = parent_time + pd.Timedelta(minutes=15)
                break_even_arm_count += 1
        elif direction < 0 and float(parent["low"]) <= entry_price * (
            1.0 - break_even_trigger_percent / 100.0
        ):
            updated = min(stop_price, entry_price * (1.0 - break_even_offset_percent / 100.0))
            if updated < stop_price:
                stop_price = updated
                break_even_armed_at = parent_time + pd.Timedelta(minutes=15)
                break_even_arm_count += 1

        if exit_reason == "period_end" and parent_time == canonical_exit_time:
            replay_exit_time = parent_time
            replay_exit_price = float(parent["close"])
            replay_reason = "period_end"
            break

    if replay_exit_time is None or replay_exit_price is None or replay_reason is None:
        raise RuntimeError(f"trade did not exit in 3m replay: {trade['trade_id']}")

    price_ratio = replay_exit_price / entry_price
    gross_return = direction * (price_ratio - 1.0)
    commission_return = 0.001 * (1.0 + price_ratio)
    net_return = gross_return - commission_return
    return {
        "trade_id": trade["trade_id"],
        "direction": trade["direction"],
        "entry_time": entry_time.isoformat(),
        "canonical_exit_time": canonical_exit_time.isoformat(),
        "replay_exit_time": replay_exit_time.isoformat(),
        "canonical_exit_price": float(trade["exit_price"]),
        "replay_exit_price": replay_exit_price,
        "canonical_exit_reason": exit_reason,
        "replay_exit_reason": replay_reason,
        "canonical_net_return": float(trade["net_return"]),
        "replay_net_return": net_return,
        "net_return_delta_bp": (net_return - float(trade["net_return"])) * 10_000.0,
        "exit_price_delta": replay_exit_price - float(trade["exit_price"]),
        "same_parent_15m_exit": replay_exit_time.floor("15min") == canonical_exit_time,
        "exact_exit_price": bool(np.isclose(replay_exit_price, float(trade["exit_price"]), atol=1e-12)),
        "trigger_subbar": None if trigger_subbar is None else trigger_subbar.isoformat(),
        "break_even_armed_at": (
            None if break_even_armed_at is None else break_even_armed_at.isoformat()
        ),
        "break_even_arm_count": break_even_arm_count,
    }


def main() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    safe_end = _utc(config["time_contract"]["safe_end_exclusive"])
    holdout_start = _utc(config["time_contract"]["holdout_start"])
    three_minute, quality = load_three_minute_prefix(config)
    parent_check = compare_parent_bars(
        three_minute,
        safe_end=safe_end,
        holdout_start=holdout_start,
    )
    parents = aggregate_three_to_fifteen(three_minute)

    trades = pd.read_csv(TRADES_PATH)
    canonical = trades.loc[
        trades["variant"].eq("v9_locked")
        & trades["split"].eq("final_preholdout_2025_202602")
    ].copy()
    if canonical.empty:
        raise RuntimeError("canonical V9 final-preholdout trades are missing")
    rows = [
        replay_trade(trade, subbars=three_minute, parents=parents)
        for _, trade in canonical.iterrows()
    ]
    replay = pd.DataFrame(rows)
    replay.to_csv(OUTPUT_CSV, index=False)

    time_matches = replay["same_parent_15m_exit"].astype(bool)
    price_matches = replay["exact_exit_price"].astype(bool)
    delta = replay["net_return_delta_bp"].astype(float)
    summary = {
        "audit": "ordered 3-minute path sensitivity for frozen V9 final-preholdout trades",
        "signal_and_barrier_parameters_changed": False,
        "holdout_consumed_for_strategy_evaluation": False,
        "data_quality": quality,
        "parent_bar_reconstruction": parent_check,
        "canonical_trade_count": int(len(canonical)),
        "same_15m_exit_parent_count": int(time_matches.sum()),
        "exact_exit_price_count": int(price_matches.sum()),
        "earlier_or_later_parent_exit_count": int((~time_matches).sum()),
        "changed_exit_price_count": int((~price_matches).sum()),
        "maximum_absolute_exit_price_delta": float(replay["exit_price_delta"].abs().max()),
        "maximum_absolute_net_return_delta_bp": float(delta.abs().max()),
        "mean_net_return_delta_bp": float(delta.mean()),
        "canonical_mean_net_bp": float(replay["canonical_net_return"].mean() * 10_000.0),
        "replay_mean_net_bp": float(replay["replay_net_return"].mean() * 10_000.0),
        "stop_trade_count": int(replay["canonical_exit_reason"].eq("stop").sum()),
        "break_even_armed_trade_count": int(replay["break_even_arm_count"].gt(0).sum()),
        "tradingview_bar_magnifier_parity_passed": False,
        "interpretation": (
            "This audit can reject hidden 15m path optimism on the same OKX feed. "
            "It cannot replace a TradingView venue-specific exported ledger."
        ),
        "operational_incident": {
            "post_holdout_rows_used_in_any_calculation": 0,
            "note": (
                "Before this bounded loader was run, an exploratory shell tail displayed two "
                "post-holdout raw 3m rows. They were not loaded into Python, scored, or used to "
                "select/evaluate any strategy configuration. The disclosure is retained because "
                "the repository treats any holdout look as material."
            ),
        },
    }
    OUTPUT_JSON.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
