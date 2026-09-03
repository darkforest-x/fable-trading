#!/usr/bin/env python3
"""Reconstruct the two owner-provided BTC 1h anchors from official OKX bars.

This is a qualitative specification replay, not a holdout performance test.
Only the two timestamps supplied through the owner's TradingView chart are
measured. No scan, aggregate score, parameter choice or model evaluation is
performed on bars at or after the repository holdout boundary.

All indicator inputs are causal at their bar: OHLCV, SMA40(HL2), the six-MA
context rope, MA Shift public-formula oscillator and confirmed 10/10 Market
Break state. A trade that uses the finished K2 morphology can enter no earlier
than the next open; the exact K2 extreme is then known and auditable.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Rectangle

from scripts.research_two_key_candle_ma_retest_1h import (
    add_features,
    direction_columns,
    path_features,
    sha256_file,
)
from scripts.research_two_key_candle_ma_retest_sma40_v2 import add_anchor_score


PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / "experiments/active/exp-two-key-candle-feature-atlas-v3/results"
API = "https://www.okx.com/api/v5/market/history-candles"

ANCHORS: tuple[dict[str, Any], ...] = (
    {
        "name": "short",
        "direction": -1,
        "k1_time": "2026-09-01T08:00:00Z",
        "k2_time": "2026-09-01T14:00:00Z",
        "drawing_entry": 78038.1,
        "drawing_target": 76196.1,
        "drawing_stop": 78386.8,
    },
    {
        "name": "long",
        "direction": 1,
        "k1_time": "2026-09-03T02:00:00Z",
        "k2_time": "2026-09-03T05:00:00Z",
        "drawing_entry": 77771.9,
        "drawing_target": 81253.2,
        "drawing_stop": 77068.0,
    },
)

TEAL = "#199D91"
ORANGE = "#D88A32"
BRIGHT_TEAL = "#25BCAF"
DARK_TEAL = "#2C7E78"
YELLOW = "#D5AD37"
INK = "#26323A"
BLUE = "#315A7D"
GOLD = "#D6A249"
GREY = "#AAB3B8"
GRID = "#D9DEE1"


def fetch_hourly(start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DataFrame, list[str]]:
    cursor = int(end.timestamp() * 1000)
    start_ms = int(start.timestamp() * 1000)
    records: dict[int, list[str]] = {}
    urls: list[str] = []
    for _ in range(40):
        query = urlencode(
            {
                "instId": "BTC-USDT-SWAP",
                "bar": "1H",
                "after": str(cursor),
                "limit": "100",
            }
        )
        url = f"{API}?{query}"
        request = Request(url, headers={"User-Agent": "fable-trading-owner-anchor-replay/1.0"})
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if str(payload.get("code")) != "0":
            raise RuntimeError(f"OKX returned {payload}")
        data = payload.get("data", [])
        if not data:
            break
        urls.append(url)
        for row in data:
            records[int(row[0])] = row
        oldest = min(int(row[0]) for row in data)
        if oldest <= start_ms:
            break
        if oldest >= cursor:
            raise RuntimeError("OKX pagination cursor did not move backwards")
        cursor = oldest
        time.sleep(0.08)
    if not records:
        raise RuntimeError("OKX returned no candles")
    rows = []
    for timestamp, row in sorted(records.items()):
        rows.append(
            {
                "open_time": pd.Timestamp(timestamp, unit="ms", tz="UTC"),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "volume_ccy": float(row[6]),
                "volume_quote": float(row[7]),
                "confirm": int(row[8]),
            }
        )
    frame = pd.DataFrame(rows)
    frame = frame[
        frame["open_time"].ge(start)
        & frame["open_time"].lt(end)
        & frame["confirm"].eq(1)
    ].drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    if frame.empty:
        raise RuntimeError("no confirmed candles in requested range")
    expected = pd.date_range(frame["open_time"].min(), frame["open_time"].max(), freq="1h", tz="UTC")
    if len(expected) != len(frame) or not frame["open_time"].reset_index(drop=True).equals(pd.Series(expected)):
        raise ValueError("official OKX hourly series contains a gap")
    return frame, urls


def pair_row(featured: pd.DataFrame, anchor: dict[str, Any]) -> dict[str, Any]:
    direction = int(anchor["direction"])
    k1_time = pd.Timestamp(anchor["k1_time"])
    k2_time = pd.Timestamp(anchor["k2_time"])
    lookup = pd.Series(featured.index, index=featured["open_time"])
    if k1_time not in lookup.index or k2_time not in lookup.index:
        raise KeyError(f"anchor timestamps unavailable: {anchor['name']}")
    k1_i = int(lookup.loc[k1_time])
    k2_i = int(lookup.loc[k2_time])
    if k2_i + 1 >= len(featured):
        raise ValueError("next-open candle unavailable")
    side = direction_columns(featured, direction)
    k1_columns = [column for column in side.columns if column.startswith("k1_")]
    k2_columns = [column for column in side.columns if column.startswith("k2_")]
    shared_columns = [
        "rope_width_atr",
        "rope_slope_side_atr",
        "prior_rope_width_atr_20",
        "prior_range_atr_20",
        "side_ma_alignment",
        "atr_release_24",
        "atr_pct",
        "green_volume_share_20",
        "ma_shift_osc",
        "ma_shift_osc_delta",
    ]
    row: dict[str, Any] = {
        "name": anchor["name"],
        "symbol": "BTC",
        "direction": direction,
        "side": "long" if direction > 0 else "short",
        "k1_i": k1_i,
        "k2_i": k2_i,
        "gap_bars": k2_i - k1_i,
        "k1_time": k1_time,
        "k2_time": k2_time,
        "k1_open": float(featured.loc[k1_i, "open"]),
        "k1_high": float(featured.loc[k1_i, "high"]),
        "k1_low": float(featured.loc[k1_i, "low"]),
        "k1_close": float(featured.loc[k1_i, "close"]),
        "k2_open": float(featured.loc[k2_i, "open"]),
        "k2_high": float(featured.loc[k2_i, "high"]),
        "k2_low": float(featured.loc[k2_i, "low"]),
        "k2_close": float(featured.loc[k2_i, "close"]),
        "atr": float(featured.loc[k2_i, "atr"]),
        "utc_hour": int(k2_time.hour),
        "weekday": int(k2_time.weekday()),
    }
    for column in k1_columns:
        row[column] = side.loc[k1_i, column]
    for column in k2_columns + shared_columns:
        row[column] = side.loc[k2_i, column]
    row["k2_to_k1_volume_ratio"] = float(featured.loc[k2_i, "volume"]) / float(featured.loc[k1_i, "volume"])
    row.update(path_features(featured, k1_i, k2_i, direction))
    next_open = float(featured.loc[k2_i + 1, "open"])
    exact_stop = float(featured.loc[k2_i, "low"] if direction > 0 else featured.loc[k2_i, "high"])
    risk = direction * (next_open - exact_stop)
    row["entry_time"] = featured.loc[k2_i + 1, "open_time"]
    row["entry_price"] = next_open
    row["stop_price"] = exact_stop
    row["stop_distance_atr_24"] = risk / float(featured.loc[k2_i, "atr"])
    row["drawing_entry"] = float(anchor["drawing_entry"])
    row["drawing_target"] = float(anchor["drawing_target"])
    row["drawing_stop"] = float(anchor["drawing_stop"])
    row["drawing_entry_minus_causal"] = float(anchor["drawing_entry"]) - next_open
    row["drawing_stop_minus_exact"] = float(anchor["drawing_stop"]) - exact_stop
    scored = add_anchor_score(pd.DataFrame([row])).iloc[0]
    for column in (
        "anchor_k1_score",
        "anchor_k2_score",
        "anchor_path_score",
        "anchor_state_score",
        "anchor_score",
    ):
        row[column] = float(scored[column])
    return row


def candle_rows(featured: pd.DataFrame, pairs: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for pair in pairs.to_dict("records"):
        for key, index in (("K1", int(pair["k1_i"])), ("K2", int(pair["k2_i"]))):
            direction = int(pair["direction"])
            side = direction_columns(featured, direction)
            rows.append(
                {
                    "setup": pair["name"],
                    "key_candle": key,
                    "time_utc": featured.loc[index, "open_time"],
                    "time_asia_shanghai": featured.loc[index, "open_time"].tz_convert("Asia/Shanghai"),
                    "open": featured.loc[index, "open"],
                    "high": featured.loc[index, "high"],
                    "low": featured.loc[index, "low"],
                    "close": featured.loc[index, "close"],
                    "sma40_hl2": featured.loc[index, "sma40_hl2"],
                    "rope_low": featured.loc[index, "rope_low"],
                    "rope_high": featured.loc[index, "rope_high"],
                    "atr": featured.loc[index, "atr"],
                    "range_atr": featured.loc[index, "range_atr"],
                    "body_ratio": featured.loc[index, "body_ratio"],
                    "directional_close_location": side.loc[index, "k1_close_location"],
                    "rejection_wick_share": side.loc[index, "k2_wick_share"],
                    "volume_ratio_20": featured.loc[index, "volume_ratio_20"],
                    "ma_shift_candle_aligned": bool(side.loc[index, "k2_ma_colour_aligned"]),
                    "native_candle_aligned": bool(side.loc[index, "k2_native_colour_aligned"]),
                    "oscillator_side_value": side.loc[index, "ma_shift_osc"],
                    "oscillator_side_delta": side.loc[index, "ma_shift_osc_delta"],
                    "oscillator_sign_aligned": bool(side.loc[index, "k2_osc_sign_aligned"]),
                    "oscillator_acceleration_aligned": bool(side.loc[index, "k2_osc_accel_aligned"]),
                    "market_break_state": featured.loc[index, "market_break_state"],
                    "market_break_aligned": bool(side.loc[index, "k2_structure_aligned"]),
                    "sma40_cross_depth_atr": side.loc[index, "k1_sma40_cross_depth_atr"],
                    "sma40_touch_depth_atr": side.loc[index, "k2_sma40_touch_depth_atr"],
                    "sma40_close_side_atr": side.loc[index, "k2_sma40_close_side_atr"],
                }
            )
    return pd.DataFrame(rows)


def oscillator_colour(value: float, delta: float) -> str:
    if value > 0 and delta > 0:
        return BRIGHT_TEAL
    if value > 0:
        return DARK_TEAL
    if value < 0 and delta < 0:
        return YELLOW
    return ORANGE


def plot_setup(
    price_axis: plt.Axes,
    osc_axis: plt.Axes,
    featured: pd.DataFrame,
    pair: pd.Series,
) -> None:
    k1_i = int(pair["k1_i"])
    k2_i = int(pair["k2_i"])
    left = max(0, k1_i - 8)
    right = min(len(featured), k2_i + (31 if pair["name"] == "short" else 23))
    data = featured.iloc[left:right].copy().reset_index(drop=True)
    x = np.arange(len(data))
    price_axis.fill_between(x, data["rope_low"], data["rope_high"], color=GREY, alpha=0.16, label="six-MA context rope")
    price_axis.plot(x, data["sma40_hl2"], color=BLUE, linewidth=1.8, label="SMA40(HL2)")
    for i, row in data.iterrows():
        ma_colour = TEAL if (row.high + row.low) / 2 >= row.sma40_hl2 else ORANGE
        price_axis.vlines(i, row.low, row.high, color=INK, linewidth=0.8, zorder=3)
        lower = min(row.open, row.close)
        height = max(abs(row.close - row.open), 0.6)
        price_axis.add_patch(Rectangle((i - 0.32, lower), 0.64, height, facecolor=ma_colour, edgecolor=INK, linewidth=0.55, zorder=4))
    local_k1 = k1_i - left
    local_k2 = k2_i - left
    for local, label, colour in ((local_k1, "K1", BLUE), (local_k2, "K2", GOLD)):
        row = data.iloc[local]
        pad = (data["high"].max() - data["low"].min()) * 0.02
        price_axis.add_patch(Rectangle((local - 0.48, row.low - pad), 0.96, row.high - row.low + 2 * pad, fill=False, edgecolor=colour, linewidth=2.1, zorder=6))
        price_axis.text(local, row.high + pad * 1.3, label, ha="center", va="bottom", color=colour, fontweight="bold")
    entry_local = local_k2 + 1
    price_axis.scatter([entry_local], [pair["entry_price"]], marker=">", s=80, color=BLUE, edgecolor=INK, linewidth=0.6, zorder=7, label="causal next-open entry")
    price_axis.axhline(pair["stop_price"], color=INK, linewidth=1.0, linestyle="--", label="exact K2 stop")
    price_axis.axhline(pair["drawing_stop"], color=ORANGE, linewidth=0.9, linestyle=":", label="drawing stop")
    price_axis.axhline(pair["drawing_target"], color=GOLD, linewidth=0.9, linestyle="-.", label="drawing target")
    price_axis.set_title(
        f"{pair['name'].upper()} — K1→K2 gap {int(pair['gap_bars'])}h, anchor score {pair['anchor_score']:.1f}",
        loc="left",
        fontsize=13,
        color=INK,
    )
    price_axis.set_ylabel("BTC-USDT-SWAP price")
    price_axis.grid(axis="y", color=GRID, linewidth=0.65)
    price_axis.spines[["top", "right"]].set_visible(False)

    osc = data["ma_shift_osc"]
    delta = data["ma_shift_osc_delta"]
    colours = [oscillator_colour(float(v), float(d)) if np.isfinite(v) and np.isfinite(d) else GREY for v, d in zip(osc, delta)]
    osc_axis.bar(x, osc, color=colours, edgecolor=INK, linewidth=0.25)
    osc_axis.axhline(0, color=INK, linewidth=0.8)
    osc_axis.axvline(local_k1, color=BLUE, linewidth=1.2)
    osc_axis.axvline(local_k2, color=GOLD, linewidth=1.2)
    tick_step = max(1, len(data) // 8)
    ticks = x[::tick_step]
    labels = [timestamp.tz_convert("Asia/Shanghai").strftime("%m-%d\n%H:%M") for timestamp in data.loc[ticks, "open_time"]]
    osc_axis.set_xticks(ticks, labels)
    osc_axis.set_ylabel("MA Shift osc")
    osc_axis.set_xlabel("Asia/Shanghai time")
    osc_axis.grid(axis="y", color=GRID, linewidth=0.65)
    osc_axis.spines[["top", "right"]].set_visible(False)


def plot_replay(featured: pd.DataFrame, pairs: pd.DataFrame, output: Path) -> None:
    fig = plt.figure(figsize=(15, 13))
    grid = fig.add_gridspec(4, 1, height_ratios=[3.0, 1.1, 3.0, 1.1])
    axes = [fig.add_subplot(grid[index]) for index in range(4)]
    plot_setup(axes[0], axes[1], featured, pairs[pairs["name"].eq("short")].iloc[0])
    plot_setup(axes[2], axes[3], featured, pairs[pairs["name"].eq("long")].iloc[0])
    handles, labels = axes[0].get_legend_handles_labels()
    dedup = dict(zip(labels, handles))
    fig.subplots_adjust(top=0.91, bottom=0.06, left=0.065, right=0.985, hspace=0.34)
    fig.legend(dedup.values(), dedup.keys(), loc="upper center", bbox_to_anchor=(0.5, 0.954), ncol=6, frameon=False)
    fig.suptitle(
        "Owner-provided BTC 1h anchors reconstructed from official OKX candles",
        x=0.01,
        y=0.985,
        ha="left",
        fontsize=16,
        color=INK,
    )
    fig.savefig(output, dpi=180, facecolor="white")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--start", default="2026-07-10T00:00:00Z")
    parser.add_argument("--end", default="2026-09-04T08:00:00Z")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end)
    raw, urls = fetch_hourly(start, end)
    raw_path = args.output / "owner_anchor_okx_1h.csv.gz"
    raw.to_csv(raw_path, index=False, compression={"method": "gzip", "mtime": 0})
    featured = add_features(raw)
    pairs = pd.DataFrame([pair_row(featured, anchor) for anchor in ANCHORS])
    candles = candle_rows(featured, pairs)
    pairs.to_csv(args.output / "owner_anchor_pairs.csv", index=False)
    candles.to_csv(args.output / "owner_anchor_candles.csv", index=False)
    plot_replay(featured, pairs, args.output / "owner_anchor_replay.png")
    receipt = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "qualitative owner-anchor specification replay only; not holdout evaluation",
        "instrument": "BTC-USDT-SWAP",
        "bar": "1H",
        "endpoint": API,
        "request_count": len(urls),
        "requested_start": start.isoformat(),
        "requested_end_exclusive": end.isoformat(),
        "confirmed_rows": int(len(raw)),
        "first_time": raw["open_time"].min().isoformat(),
        "last_time": raw["open_time"].max().isoformat(),
        "csv_path": str(raw_path.relative_to(PROJECT)),
        "csv_sha256": sha256_file(raw_path),
        "script_path": str(Path(__file__).resolve().relative_to(PROJECT)),
        "script_sha256": sha256_file(Path(__file__).resolve()),
        "anchors": [
            {"name": row["name"], "k1_time": str(row["k1_time"]), "k2_time": str(row["k2_time"]), "anchor_score": float(row["anchor_score"])}
            for row in pairs.to_dict("records")
        ],
    }
    (args.output / "owner_anchor_source_receipt.json").write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
