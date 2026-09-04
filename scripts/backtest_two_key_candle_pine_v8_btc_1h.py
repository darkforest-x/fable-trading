#!/usr/bin/env python3
"""Replay the frozen Pine-v8 K1->K2 rule on BTC-USDT-SWAP 1h bars.

Signal features are causal.  At candidate K2 bar ``t`` this module uses only
``open/high/low/close/volume/open_time`` through ``t``: Pine/Wilder ATR14,
SMA40(HL2), close-derived SMA/EMA 20/60/120, the public MA-Shift oscillator
(1000-bar 99th percentile, 15-bar change, HMA10), confirmed 10/10 pivots,
K1/K2 candle geometry, and the intervening K1->K2 path.  K1 is searched only
2--8 bars behind K2.  Entry is the next bar open, so the completed K2 extreme
is known before the exact stop is accepted.

Future rows are read only by the frozen execution replay: exact K2 stop, 3R
target, 12 one-hour bars, conservative stop on same-bar collisions, barrier
price gap idealization, linear long/short returns, and one 0.2% round-trip cost.
The source snapshot ends at the preregistered UTC boundary.  Incomplete tail
paths remain ``unresolved`` and never enter scored metrics.

This is configuration-specific holdout use 1, explicitly authorized by the
owner on 2026-09-04.  It does not tune a threshold, fit a model, promote an
artifact, mutate ACTIVE/frozen/forward state, message anyone, or place orders.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Rectangle
from scipy.stats import fisher_exact

from scripts.research_two_key_candle_ma_retest_1h import (
    add_features,
    direction_columns,
    path_features,
    profit_factor,
    sha256_file,
)

PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = (
    PROJECT
    / "experiments/active/exp-btcusdtp-1h-pine-v8-sixmonth-backtest-20260904-v1"
)
CONFIG_PATH = EXPERIMENT / "config.json"
RESULTS = EXPERIMENT / "results"
SOURCE_DIR = RESULTS / "source"
SOURCE_PATH = SOURCE_DIR / "okx_BTC_USDT_SWAP_1H.csv.gz"
PINE_PATH = (
    PROJECT
    / "experiments/active/exp-two-key-candle-feature-atlas-v3/pine"
    / "fable_two_key_candle_sma40_retest_v1.pine"
)
API = "https://www.okx.com/api/v5/market/history-candles"
BAR_DURATION = pd.Timedelta(hours=1)

TEAL = "#17A297"
ORANGE = "#F59E0B"
INK = "#26323A"
BLUE = "#315A7D"
MUTED = "#7A858D"
GRID = "#D9DEE1"
LIGHT_TEAL = "#DDF3F0"
LIGHT_ORANGE = "#FFF0D8"


def load_config() -> dict[str, Any]:
    """Load the preregistered experiment contract."""

    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _utc(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _json_value(value: Any) -> Any:
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return _utc(value).isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json_value(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def fetch_official_hourly(config: dict[str, Any], output: Path) -> dict[str, Any]:
    """Freeze confirmed official OKX 1H rows inside the experiment directory."""

    start = _utc(config["window"]["warmup_start_inclusive"])
    end = _utc(config["window"]["snapshot_end_exclusive"])
    start_ms = int(start.timestamp() * 1000)
    cursor = int(end.timestamp() * 1000)
    records: dict[int, list[str]] = {}
    request_urls: list[str] = []
    for _ in range(140):
        query = urlencode(
            {
                "instId": config["instrument"]["okx_instrument"],
                "bar": config["instrument"]["bar"],
                "after": str(cursor),
                "limit": "100",
            }
        )
        url = f"{API}?{query}"
        request = Request(url, headers={"User-Agent": "fable-trading-btc-1h-backtest/1.0"})
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if str(payload.get("code")) != "0":
            raise RuntimeError(f"OKX returned code={payload.get('code')} msg={payload.get('msg')}")
        page = payload.get("data") or []
        if not page:
            break
        request_urls.append(url)
        for row in page:
            records[int(row[0])] = row
        oldest = min(int(row[0]) for row in page)
        if oldest <= start_ms:
            break
        if oldest >= cursor:
            raise RuntimeError("OKX pagination cursor did not move backwards")
        cursor = oldest
        time.sleep(0.10)
    else:
        raise RuntimeError("OKX pagination exceeded the preregistered safety bound")

    if not records:
        raise RuntimeError("OKX returned no hourly candles")
    rows: list[dict[str, Any]] = []
    for timestamp, row in sorted(records.items()):
        rows.append(
            {
                "ts": timestamp,
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "open_time": pd.Timestamp(timestamp, unit="ms", tz="UTC"),
                "confirm": int(row[8]),
            }
        )
    frame = pd.DataFrame(rows)
    frame = frame[
        frame["open_time"].ge(start)
        & frame["open_time"].lt(end)
        & frame["confirm"].eq(1)
    ].copy()
    frame = frame.sort_values("open_time", kind="mergesort").drop_duplicates(
        "open_time", keep="last"
    )
    frame = frame.reset_index(drop=True)
    if frame.empty:
        raise RuntimeError("no confirmed OKX rows inside the frozen window")
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(
        output,
        index=False,
        compression={"method": "gzip", "mtime": 0},
    )
    receipt = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "endpoint": API,
        "instrument": config["instrument"]["okx_instrument"],
        "bar": config["instrument"]["bar"],
        "requested_start_inclusive": start,
        "requested_end_exclusive": end,
        "request_count": len(request_urls),
        "confirmed_rows": len(frame),
        "first_open_time": frame["open_time"].iloc[0],
        "last_open_time": frame["open_time"].iloc[-1],
        "snapshot_path": output.relative_to(PROJECT),
        "snapshot_sha256": sha256_file(output),
        "request_first": request_urls[0] if request_urls else None,
        "request_last": request_urls[-1] if request_urls else None,
        "production_cache_overwritten": False,
    }
    write_json(RESULTS / "source_receipt.json", receipt)
    return receipt


def load_hourly_source(path: Path, config: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Read, normalize, and fail closed on the exact one-hour OHLCV grain."""

    frame = pd.read_csv(path)
    required = {"open", "high", "low", "close", "volume", "open_time"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"source missing required columns: {missing}")
    original_rows = len(frame)
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True, errors="raise")
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    if "confirm" in frame.columns:
        frame = frame[pd.to_numeric(frame["confirm"], errors="raise").eq(1)].copy()
    duplicate_rows = int(frame["open_time"].duplicated(keep=False).sum())
    if duplicate_rows:
        value_columns = [column for column in required if column != "open_time"]
        conflicting = []
        for stamp, group in frame[frame["open_time"].duplicated(keep=False)].groupby(
            "open_time", sort=True
        ):
            if len(group[value_columns].drop_duplicates()) != 1:
                conflicting.append(stamp.isoformat())
        if conflicting:
            raise ValueError(f"conflicting duplicate hourly bars: {conflicting[:5]}")
        frame = frame.drop_duplicates("open_time", keep="last")
    frame = frame.sort_values("open_time", kind="mergesort").reset_index(drop=True)
    start = _utc(config["window"]["warmup_start_inclusive"])
    end = _utc(config["window"]["snapshot_end_exclusive"])
    frame = frame[frame["open_time"].ge(start) & frame["open_time"].lt(end)].reset_index(drop=True)
    if frame.empty:
        raise ValueError("source has no rows inside the frozen snapshot window")
    diffs = frame["open_time"].diff().dropna()
    gap_mask = diffs.ne(BAR_DURATION)
    invalid_ohlc = (
        frame[["open", "high", "low", "close"]].le(0.0).any(axis=1)
        | frame["high"].lt(frame[["open", "close"]].max(axis=1))
        | frame["low"].gt(frame[["open", "close"]].min(axis=1))
        | frame["high"].lt(frame["low"])
    )
    negative_volume = frame["volume"].lt(0.0)
    expected_first = start
    expected_last = end - BAR_DURATION
    quality = {
        "source_path": path.relative_to(PROJECT) if path.is_relative_to(PROJECT) else path,
        "source_sha256": sha256_file(path),
        "original_rows": original_rows,
        "confirmed_rows_in_window": len(frame),
        "duplicate_rows_seen": duplicate_rows,
        "gap_count": int(gap_mask.sum()),
        "invalid_ohlc_rows": int(invalid_ohlc.sum()),
        "negative_volume_rows": int(negative_volume.sum()),
        "first_open_time": frame["open_time"].iloc[0],
        "last_open_time": frame["open_time"].iloc[-1],
        "expected_first_open_time": expected_first,
        "expected_last_open_time": expected_last,
        "covers_frozen_window": bool(
            frame["open_time"].iloc[0] == expected_first
            and frame["open_time"].iloc[-1] == expected_last
        ),
    }
    if quality["gap_count"]:
        first_gap = frame.loc[gap_mask[gap_mask].index[0], "open_time"]
        raise ValueError(f"official hourly source contains a gap ending at {first_gap}")
    if quality["invalid_ohlc_rows"] or quality["negative_volume_rows"]:
        raise ValueError(f"invalid OHLCV rows: {quality}")
    if not quality["covers_frozen_window"]:
        raise ValueError(f"source does not cover preregistered frozen window: {quality}")
    return frame, quality


def _load_crosscheck(path: Path, *, aggregate_15m: bool = False) -> pd.DataFrame:
    frame = pd.read_csv(path, usecols=["open", "high", "low", "close", "volume", "open_time"])
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True, errors="raise")
    frame = frame.sort_values("open_time").drop_duplicates("open_time", keep="last")
    if not aggregate_15m:
        return frame.reset_index(drop=True)
    grouped = frame.set_index("open_time").resample("1h", label="left", closed="left", origin="epoch")
    hourly = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        source_rows=("close", "count"),
    )
    return hourly[hourly["source_rows"].eq(4)].drop(columns="source_rows").dropna().reset_index()


def crosscheck_sources(primary: pd.DataFrame) -> list[dict[str, Any]]:
    """Compare the official pull with all preregistered overlapping snapshots."""

    sources = (
        (PROJECT / "data/kline_fetched/okx_BTC_USDT_SWAP_1H_9699.csv", False),
        (PROJECT / "data/kline_fetched/okx_BTC_USDT_SWAP_15m_42007.csv", True),
        (
            PROJECT
            / "experiments/active/exp-1h-okx-model-first-standing-top10-20260904-v1"
            / "results/candles/BTC_USDT_SWAP.csv",
            False,
        ),
    )
    rows: list[dict[str, Any]] = []
    for path, aggregate in sources:
        other = _load_crosscheck(path, aggregate_15m=aggregate)
        common = primary.merge(other, on="open_time", suffixes=("_primary", "_other"), how="inner")
        detail: dict[str, Any] = {
            "source_path": path.relative_to(PROJECT),
            "source_sha256": sha256_file(path),
            "aggregation": "complete UTC 15m groups to 1h" if aggregate else "native 1h",
            "source_rows": len(other),
            "overlap_rows": len(common),
            "overlap_first": common["open_time"].min() if len(common) else None,
            "overlap_last": common["open_time"].max() if len(common) else None,
        }
        for column in ("open", "high", "low", "close", "volume"):
            if common.empty:
                detail[f"{column}_mismatch_rows"] = None
                detail[f"{column}_max_abs_diff"] = None
                continue
            difference = (
                pd.to_numeric(common[f"{column}_primary"]).astype(float)
                - pd.to_numeric(common[f"{column}_other"]).astype(float)
            ).abs()
            tolerance = 1e-8 if column != "volume" else 0.05
            detail[f"{column}_mismatch_rows"] = int(difference.gt(tolerance).sum())
            detail[f"{column}_max_abs_diff"] = float(difference.max())
        detail["ohlc_exact_match"] = bool(
            len(common)
            and all(detail[f"{column}_mismatch_rows"] == 0 for column in ("open", "high", "low", "close"))
        )
        rows.append(detail)
    return rows


def _clamp01(value: float) -> float:
    return float(np.clip(value if np.isfinite(value) else 0.0, 0.0, 1.0))


def _unit(value: float, low: float, high: float) -> float:
    return _clamp01((value - low) / (high - low))


def _finite(*values: object) -> bool:
    return all(np.isfinite(float(value)) for value in values)


def _candidate_row(
    featured: pd.DataFrame,
    side: pd.DataFrame,
    *,
    direction: int,
    k1_i: int,
    k2_i: int,
    gap: int,
) -> dict[str, Any]:
    path = path_features(featured, k1_i, k2_i, direction)
    k1_body_ratio = float(side.loc[k1_i, "k1_body_ratio"])
    k1_range_atr = float(side.loc[k1_i, "k1_range_atr"])
    k1_close_location = float(side.loc[k1_i, "k1_close_location"])
    k1_cross_depth = float(side.loc[k1_i, "k1_sma40_cross_depth_atr"])
    k1_volume_ratio = float(side.loc[k1_i, "k1_volume_ratio"])
    k1_quality = float(
        np.mean(
            [
                min(1.0, float(side.loc[k1_i, "k1_rope_coverage"])),
                min(1.0, max(0.0, k1_body_ratio)),
                min(1.0, max(0.0, k1_range_atr / 2.0)),
                min(1.0, max(0.0, (float(side.loc[k1_i, "k1_cross_depth_atr"]) + 0.15) / 0.50)),
            ]
        )
    )
    k1_score = float(
        np.mean(
            [
                _unit(k1_body_ratio, 0.50, 0.90),
                _unit(k1_range_atr, 1.00, 2.00),
                _unit(k1_close_location, 0.75, 0.95),
                _unit(k1_volume_ratio, 1.00, 2.00),
                _unit(k1_cross_depth, -0.05, 0.35),
            ]
        )
    )
    k2_wick = float(side.loc[k2_i, "k2_wick_share"])
    k2_body = float(side.loc[k2_i, "k2_body_ratio"])
    k2_reject = float(side.loc[k2_i, "k2_rejection_close_location"])
    k2_touch = float(side.loc[k2_i, "k2_sma40_touch_depth_atr"])
    k2_close_side = float(side.loc[k2_i, "k2_sma40_close_side_atr"])
    touch_quality = math.exp(-((k2_touch - 0.40) / 0.45) ** 2)
    k2_five_sum = float(
        _unit(k2_wick, 0.45, 0.90)
        + _unit(0.50 - k2_body, 0.00, 0.40)
        + _unit(k2_reject, 0.65, 0.95)
        + _clamp01(touch_quality)
        + _unit(k2_close_side, 0.00, 0.75)
    )
    volume_relation = float(featured.loc[k2_i, "volume"]) / max(
        float(featured.loc[k1_i, "volume"]), 1e-12
    )
    volume_quality = (
        math.exp(-abs(math.log(volume_relation)) / math.log(3.0))
        if volume_relation > 0.0
        else 0.0
    )
    gap_quality = 1.0 if 3 <= gap <= 6 else 0.65
    path_score = float(
        np.mean(
            [
                gap_quality,
                _clamp01(1.0 - float(path["pre_retest_extension_atr"]) / 1.50),
                1.0 if int(path["wrong_sma40_close_count"]) == 0 else 0.0,
                _clamp01(float(path["intermediate_ma_colour_share"])),
                math.exp(-abs(float(path["close_distance_atr"])) / 0.75),
                _clamp01(volume_quality),
            ]
        )
    )
    oscillator_aligned = (
        bool(side.loc[k1_i, "k1_osc_accel_aligned"])
        and bool(side.loc[k2_i, "k2_osc_sign_aligned"])
        and not bool(side.loc[k2_i, "k2_osc_accel_aligned"])
    )
    state_score = float(
        np.mean(
            [
                float(bool(side.loc[k1_i, "k1_ma_colour_aligned"])),
                float(bool(side.loc[k2_i, "k2_ma_colour_aligned"])),
                float(bool(side.loc[k1_i, "k1_osc_accel_aligned"])),
                float(bool(side.loc[k2_i, "k2_osc_sign_aligned"])),
                float(not bool(side.loc[k2_i, "k2_osc_accel_aligned"])),
                float(bool(side.loc[k2_i, "k2_structure_aligned"])),
            ]
        )
    )
    row: dict[str, Any] = {
        "direction": direction,
        "side": "long" if direction > 0 else "short",
        "k1_i": k1_i,
        "k2_i": k2_i,
        "gap_bars": gap,
        "k1_time": featured.loc[k1_i, "open_time"],
        "k2_time": featured.loc[k2_i, "open_time"],
        "k1_open": float(featured.loc[k1_i, "open"]),
        "k1_high": float(featured.loc[k1_i, "high"]),
        "k1_low": float(featured.loc[k1_i, "low"]),
        "k1_close": float(featured.loc[k1_i, "close"]),
        "k2_open": float(featured.loc[k2_i, "open"]),
        "k2_high": float(featured.loc[k2_i, "high"]),
        "k2_low": float(featured.loc[k2_i, "low"]),
        "k2_close": float(featured.loc[k2_i, "close"]),
        "atr": float(featured.loc[k2_i, "atr"]),
        "sma40_k1": float(featured.loc[k1_i, "sma40_hl2"]),
        "sma40_k2": float(featured.loc[k2_i, "sma40_hl2"]),
        "k1_body_ratio": k1_body_ratio,
        "k1_range_atr": k1_range_atr,
        "k1_close_location": k1_close_location,
        "k1_sma40_cross_depth_atr": k1_cross_depth,
        "k1_volume_ratio": k1_volume_ratio,
        "k1_ma_colour_aligned": bool(side.loc[k1_i, "k1_ma_colour_aligned"]),
        "k1_osc_accel_aligned": bool(side.loc[k1_i, "k1_osc_accel_aligned"]),
        "k2_wick_share": k2_wick,
        "k2_body_ratio": k2_body,
        "k2_rejection_close_location": k2_reject,
        "k2_sma40_touch_depth_atr": k2_touch,
        "k2_sma40_close_side_atr": k2_close_side,
        "k2_volume_ratio": float(side.loc[k2_i, "k2_volume_ratio"]),
        "k2_ma_colour_aligned": bool(side.loc[k2_i, "k2_ma_colour_aligned"]),
        "k2_osc_sign_aligned": bool(side.loc[k2_i, "k2_osc_sign_aligned"]),
        "k2_osc_accel_aligned": bool(side.loc[k2_i, "k2_osc_accel_aligned"]),
        "k2_structure_aligned": bool(side.loc[k2_i, "k2_structure_aligned"]),
        "side_ma_alignment": float(side.loc[k2_i, "side_ma_alignment"]),
        "rope_slope_side_atr": float(side.loc[k2_i, "rope_slope_side_atr"]),
        "atr_pct": float(side.loc[k2_i, "atr_pct"]),
        "atr_release_24": float(side.loc[k2_i, "atr_release_24"]),
        "ma_shift_osc": float(side.loc[k2_i, "ma_shift_osc"]),
        "ma_shift_osc_delta": float(side.loc[k2_i, "ma_shift_osc_delta"]),
        "k2_to_k1_volume_ratio": volume_relation,
        "k1_quality": k1_quality,
        "anchor_k1_score": k1_score * 100.0,
        "anchor_k2_score_pre_risk": k2_five_sum / 5.0 * 100.0,
        "anchor_path_score": path_score * 100.0,
        "anchor_state_score": state_score * 100.0,
        "oscillator_sequence_aligned": oscillator_aligned,
        **path,
    }
    return row


def detect_raw_candidates(featured: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Return Pine-equivalent best-K1 candidates before next-open risk/dedupe."""

    signal = config["signal"]
    output: list[dict[str, Any]] = []
    for direction in (1, -1):
        side = direction_columns(featured, direction)
        for k2_i in range(int(signal["gap_max_bars"]), len(featured) - 1):
            k2_values = (
                side.loc[k2_i, "k2_wick_share"],
                side.loc[k2_i, "k2_body_ratio"],
                side.loc[k2_i, "k2_rejection_close_location"],
                side.loc[k2_i, "k2_sma40_touch_depth_atr"],
                side.loc[k2_i, "k2_sma40_close_side_atr"],
            )
            if not _finite(*k2_values):
                continue
            if not (
                float(k2_values[0]) >= float(signal["k2_min_rejection_wick_share"])
                and float(k2_values[1]) <= float(signal["k2_max_body_ratio"])
                and float(k2_values[2]) >= float(signal["k2_min_rejection_close_location"])
                and float(signal["k2_touch_depth_atr_min"])
                <= float(k2_values[3])
                <= float(signal["k2_touch_depth_atr_max"])
                and float(k2_values[4]) >= float(signal["k2_min_close_back_depth_atr"])
            ):
                continue
            best: dict[str, Any] | None = None
            for gap in range(int(signal["gap_min_bars"]), int(signal["gap_max_bars"]) + 1):
                k1_i = k2_i - gap
                values = (
                    side.loc[k1_i, "k1_body_ratio"],
                    side.loc[k1_i, "k1_range_atr"],
                    side.loc[k1_i, "k1_close_location"],
                    side.loc[k1_i, "k1_sma40_cross_depth_atr"],
                    featured.loc[k1_i, "rope_high"],
                    featured.loc[k1_i, "rope_low"],
                )
                if not _finite(*values):
                    continue
                directional_body = direction * (
                    float(featured.loc[k1_i, "close"]) - float(featured.loc[k1_i, "open"])
                ) > 0.0
                if not (
                    directional_body
                    and float(values[0]) >= float(signal["k1_min_body_ratio"])
                    and float(values[1]) >= float(signal["k1_min_range_atr"])
                    and float(values[2]) >= float(signal["k1_min_directional_close_location"])
                    and float(values[3]) >= float(signal["k1_min_sma40_cross_depth_atr"])
                ):
                    continue
                row = _candidate_row(
                    featured,
                    side,
                    direction=direction,
                    k1_i=k1_i,
                    k2_i=k2_i,
                    gap=gap,
                )
                if best is None or float(row["k1_quality"]) > float(best["k1_quality"]):
                    best = row
            if best is not None:
                output.append(best)
    if not output:
        return pd.DataFrame()
    return pd.DataFrame(output).sort_values(["k2_i", "direction"], ascending=[True, False]).reset_index(drop=True)


def _causal_flags(row: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    if int(row["gap_bars"]) in {2, 7, 8}:
        flags.append("edge_gap_2_7_8")
    if float(row["k1_body_ratio"]) < 0.65:
        flags.append("k1_body_below_owner_strict")
    if float(row["k1_range_atr"]) < 1.25:
        flags.append("k1_range_below_owner_strict")
    if float(row["k1_close_location"]) < 0.85:
        flags.append("k1_close_below_owner_strict")
    if float(row["k1_volume_ratio"]) < 1.25:
        flags.append("k1_volume_below_owner_strict")
    if not bool(row["k1_ma_colour_aligned"]):
        flags.append("k1_ma_colour_misaligned")
    if float(row["k2_wick_share"]) < 0.60:
        flags.append("k2_wick_below_owner_strict")
    if float(row["k2_body_ratio"]) > 0.35:
        flags.append("k2_body_above_owner_strict")
    if float(row["k2_rejection_close_location"]) < 0.65:
        flags.append("k2_rejection_below_owner_strict")
    if float(row["k2_sma40_close_side_atr"]) < 0.25:
        flags.append("k2_close_back_below_owner_strict")
    if not 0.10 <= float(row["k2_sma40_touch_depth_atr"]) <= 1.00:
        flags.append("k2_touch_outside_owner_strict")
    if not 0.25 <= float(row["stop_distance_atr"]) <= 2.00:
        flags.append("risk_outside_owner_strict")
    if abs(float(row["close_distance_atr"])) > 0.75:
        flags.append("path_close_distance_large")
    if float(row["pre_retest_extension_atr"]) > 1.00:
        flags.append("path_extension_large")
    if int(row["wrong_sma40_close_count"]) > 0:
        flags.append("path_wrong_sma40_close")
    if float(row["intermediate_ma_colour_share"]) < 1.00:
        flags.append("path_ma_colour_not_continuous")
    if not 0.50 <= float(row["k2_to_k1_volume_ratio"]) <= 1.50:
        flags.append("path_volume_relation_outside")
    if not bool(row["oscillator_sequence_aligned"]):
        flags.append("oscillator_state_misaligned")
    if not bool(row["k2_structure_aligned"]):
        flags.append("market_structure_misaligned")
    return flags


def accept_pine_events(candidates: pd.DataFrame, featured: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Apply next-open risk, long priority, remembered-K1, and cooldown state."""

    if candidates.empty:
        return candidates.copy()
    signal = config["signal"]
    by_bar_side = {
        (int(row.k2_i), int(row.direction)): row._asdict()
        for row in candidates.itertuples(index=False)
    }
    accepted: list[dict[str, Any]] = []
    last_accepted_entry = -10**12
    last_k1 = {1: None, -1: None}
    for k2_i in sorted(candidates["k2_i"].astype(int).unique()):
        entry_i = k2_i + 1
        for direction in (1, -1):
            base = by_bar_side.get((k2_i, direction))
            if base is None:
                continue
            entry_price = float(featured.loc[entry_i, "open"])
            stop_price = float(featured.loc[k2_i, "low"] if direction > 0 else featured.loc[k2_i, "high"])
            risk = direction * (entry_price - stop_price)
            risk_atr = risk / float(featured.loc[k2_i, "atr"])
            if not (
                np.isfinite(risk_atr)
                and float(signal["next_open_risk_atr_min"])
                <= risk_atr
                <= float(signal["next_open_risk_atr_max"])
            ):
                continue
            cooldown_ready = entry_i - last_accepted_entry >= int(signal["cooldown_bars"])
            k1_unused = last_k1[direction] is None or int(base["k1_i"]) != int(last_k1[direction])
            if not cooldown_ready or not k1_unused:
                continue
            row = dict(base)
            row.update(
                {
                    "entry_i": entry_i,
                    "entry_time": featured.loc[entry_i, "open_time"],
                    "entry_price": entry_price,
                    "stop_price": stop_price,
                    "risk_price": risk,
                    "stop_distance_atr": risk_atr,
                    "target_price": entry_price + direction * risk * float(config["execution"]["target_r"]),
                }
            )
            risk_quality = 1.0 if 0.25 <= risk_atr <= 2.00 else 0.0
            k2_score = (float(row["anchor_k2_score_pre_risk"]) / 100.0 * 5.0 + risk_quality) / 6.0
            row["anchor_k2_score"] = k2_score * 100.0
            row["anchor_score"] = 0.25 * (
                float(row["anchor_k1_score"])
                + float(row["anchor_k2_score"])
                + float(row["anchor_path_score"])
                + float(row["anchor_state_score"])
            )
            flags = _causal_flags(row)
            row["causal_flag_count"] = len(flags)
            row["causal_flags"] = "|".join(flags)
            row["event_id"] = hashlib.sha256(
                f"BTC-USDT-SWAP|1H|{direction}|{featured.loc[k2_i, 'open_time'].isoformat()}|{row['k1_i']}".encode()
            ).hexdigest()[:16]
            accepted.append(row)
            last_accepted_entry = entry_i
            last_k1[direction] = int(row["k1_i"])
            break
    return pd.DataFrame(accepted).sort_values("entry_i").reset_index(drop=True)


def _path_class(outcome: str, hold_bars: int, mfe_r: float, mae_r: float, net_return: float | None) -> str:
    if outcome == "unresolved":
        return "unresolved"
    if outcome == "tp":
        if hold_bars <= 4 and mae_r <= 0.50:
            return "fast_clean_tp"
        if mae_r >= 1.00:
            return "tp_after_drawdown"
        return "ordinary_tp"
    if outcome in {"sl", "sl_ambiguous"}:
        if hold_bars <= 2 and mfe_r < 0.50:
            return "immediate_reversal_sl"
        if mfe_r >= 1.50:
            return "giveback_then_sl"
        return "ordinary_sl"
    return "timeout_gain" if float(net_return or 0.0) > 0.0 else "timeout_loss"


def resolve_path(
    featured: pd.DataFrame,
    *,
    signal_i: int,
    entry_i: int,
    direction: int,
    entry_price: float,
    risk_price: float,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Resolve one exact-risk 3R/12h path without altering the signal."""

    execution = config["execution"]
    horizon = int(execution["horizon_bars"])
    target_r = float(execution["target_r"])
    cost = float(execution["round_trip_cost_fraction"])
    stop = entry_price - direction * risk_price
    target = entry_price + direction * risk_price * target_r
    available = min(horizon, len(featured) - entry_i)
    if available <= 0:
        return {
            "outcome": "unresolved",
            "resolved": False,
            "exit_i": None,
            "exit_bar_time": None,
            "exit_available_at": None,
            "exit_price": None,
            "hold_bars": 0,
            "gross_return": None,
            "net_return": None,
            "return_r": None,
            "net_return_r": None,
            "mfe_r": 0.0,
            "mae_r": 0.0,
            "path_class": "unresolved",
        }
    mfe = 0.0
    mae = 0.0
    outcome = ""
    exit_i: int | None = None
    exit_price: float | None = None
    for local, i in enumerate(range(entry_i, entry_i + available), start=1):
        high = float(featured.loc[i, "high"])
        low = float(featured.loc[i, "low"])
        favourable = high - entry_price if direction > 0 else entry_price - low
        adverse = entry_price - low if direction > 0 else high - entry_price
        mfe = max(mfe, favourable)
        mae = max(mae, adverse)
        hit_stop = low <= stop if direction > 0 else high >= stop
        hit_target = high >= target if direction > 0 else low <= target
        if hit_stop:
            outcome = "sl_ambiguous" if hit_target else "sl"
            exit_i = i
            exit_price = stop
            break
        if hit_target:
            outcome = "tp"
            exit_i = i
            exit_price = target
            break
    if exit_i is None and available < horizon:
        result = {
            "outcome": "unresolved",
            "resolved": False,
            "exit_i": None,
            "exit_bar_time": None,
            "exit_available_at": None,
            "exit_price": None,
            "hold_bars": available,
            "gross_return": None,
            "net_return": None,
            "return_r": None,
            "net_return_r": None,
            "mfe_r": mfe / risk_price,
            "mae_r": mae / risk_price,
        }
        result["path_class"] = _path_class("unresolved", available, result["mfe_r"], result["mae_r"], None)
        return result
    if exit_i is None:
        exit_i = entry_i + horizon - 1
        exit_price = float(featured.loc[exit_i, "close"])
        outcome = "timeout"
    gross = direction * (float(exit_price) / entry_price - 1.0)
    net = gross - cost
    hold_bars = exit_i - entry_i + 1
    result = {
        "outcome": outcome,
        "resolved": True,
        "exit_i": exit_i,
        "exit_bar_time": featured.loc[exit_i, "open_time"],
        "exit_available_at": featured.loc[exit_i, "open_time"] + BAR_DURATION,
        "exit_price": float(exit_price),
        "hold_bars": hold_bars,
        "gross_return": gross,
        "net_return": net,
        "return_r": direction * (float(exit_price) - entry_price) / risk_price,
        "net_return_r": net / (risk_price / entry_price),
        "mfe_r": mfe / risk_price,
        "mae_r": mae / risk_price,
    }
    result["path_class"] = _path_class(outcome, hold_bars, result["mfe_r"], result["mae_r"], net)
    return result


def attach_outcomes(events: pd.DataFrame, featured: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Attach the frozen path outcome and plain-language audit fields."""

    rows: list[dict[str, Any]] = []
    for event in events.to_dict("records"):
        result = resolve_path(
            featured,
            signal_i=int(event["k2_i"]),
            entry_i=int(event["entry_i"]),
            direction=int(event["direction"]),
            entry_price=float(event["entry_price"]),
            risk_price=float(event["risk_price"]),
            config=config,
        )
        row = {**event, **result}
        row["net_profitable"] = bool(result["resolved"] and float(result["net_return"]) > 0.0)
        if not bool(result["resolved"]):
            verdict = "unresolved"
            mechanism = (
                f"Snapshot ended after {int(result['hold_bars'])} of the required "
                f"{int(config['execution']['horizon_bars'])} path bars; excluded from scored metrics."
            )
        else:
            verdict = "success" if row["net_profitable"] else "failure"
            if result["outcome"] == "tp":
                mechanism = (
                    f"3R target was reached on path bar {int(result['hold_bars'])}; "
                    f"maximum adverse excursion was {float(result['mae_r']):.2f}R."
                )
                if not row["net_profitable"]:
                    mechanism += " Frozen round-trip cost exceeded the gross target return."
            elif result["outcome"] == "sl_ambiguous":
                mechanism = (
                    f"Stop and target were both inside path bar {int(result['hold_bars'])}; "
                    "the frozen conservative collision rule records the stop first."
                )
            elif result["outcome"] == "sl":
                mechanism = (
                    f"The exact K2-extreme stop was breached on path bar {int(result['hold_bars'])} "
                    f"after at most {float(result['mfe_r']):.2f}R favourable excursion."
                )
            else:
                sign = "above" if float(result["gross_return"]) > 0.0 else "below"
                mechanism = (
                    f"Neither barrier was reached in 12 bars; the timeout close finished {sign} entry "
                    f"with MFE {float(result['mfe_r']):.2f}R and MAE {float(result['mae_r']):.2f}R."
                )
                if float(result["gross_return"]) > 0.0 and not row["net_profitable"]:
                    mechanism += " The gross gain did not cover the frozen 0.2% cost."
        row["verdict"] = verdict
        row["outcome_reason"] = mechanism
        row["pre_entry_weaknesses"] = str(row.get("causal_flags", "")) or "none_of_frozen_flags"
        row["reason_scope"] = (
            "outcome_reason is mechanical path attribution; pre_entry_weaknesses are descriptive "
            "associations, not proven causes"
        )
        rows.append(row)
    return pd.DataFrame(rows)


def _atr_quintiles(featured: pd.DataFrame, eligible: pd.Series) -> np.ndarray:
    buckets = np.full(len(featured), -1, dtype=int)
    helper = pd.DataFrame(
        {
            "i": np.arange(len(featured)),
            "month": featured["open_time"].dt.strftime("%Y-%m"),
            "atr": featured["atr"],
            "eligible": eligible.to_numpy(dtype=bool),
        }
    )
    for _, group in helper[helper["eligible"] & helper["atr"].notna()].groupby("month", sort=True):
        ranks = group["atr"].rank(method="first")
        labels = pd.qcut(ranks, q=5, labels=False, duplicates="drop").astype(int)
        buckets[group["i"].to_numpy(dtype=int)] = labels.to_numpy(dtype=int)
    return buckets


def build_matched_controls(
    events: pd.DataFrame,
    featured: pd.DataFrame,
    config: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build exact-stratum random entries with copied side and ATR risk."""

    resolved = events[events["resolved"].astype(bool)].copy()
    if resolved.empty:
        return pd.DataFrame(), pd.DataFrame()
    analysis_start = _utc(config["window"]["analysis_start_inclusive"])
    snapshot_end = _utc(config["window"]["snapshot_end_exclusive"])
    horizon = int(config["execution"]["horizon_bars"])
    entry_times = featured["open_time"].shift(-1)
    eligible = (
        entry_times.ge(analysis_start)
        & entry_times.lt(snapshot_end)
        & featured["atr"].notna()
        & (pd.Series(np.arange(len(featured)), index=featured.index) + horizon < len(featured))
    )
    buckets = _atr_quintiles(featured, eligible)
    months = featured["open_time"].dt.strftime("%Y-%m").to_numpy()
    blocks = (featured["open_time"].dt.hour.to_numpy(dtype=int) // 6).astype(int)
    event_indices = resolved["k2_i"].astype(int).to_numpy()
    exclusion = np.zeros(len(featured), dtype=bool)
    radius = int(config["matched_control"]["exclude_within_bars_of_signal"])
    for index in event_indices:
        exclusion[max(0, index - radius) : min(len(featured), index + radius + 1)] = True
    pool: dict[tuple[str, int, int], list[int]] = {}
    for index in np.flatnonzero(eligible.to_numpy(dtype=bool) & ~exclusion & (buckets >= 0)):
        key = (str(months[index]), int(blocks[index]), int(buckets[index]))
        pool.setdefault(key, []).append(int(index))

    control_rows: list[dict[str, Any]] = []
    pair_rows: list[dict[str, Any]] = []
    required = int(config["matched_control"]["controls_per_trade"])
    seed = str(config["matched_control"]["seed"])
    for event in resolved.to_dict("records"):
        signal_i = int(event["k2_i"])
        key = (str(months[signal_i]), int(blocks[signal_i]), int(buckets[signal_i]))
        choices = pool.get(key, [])
        ranked = sorted(
            choices,
            key=lambda index: hashlib.sha256(
                f"{seed}|{event['event_id']}|{index}".encode()
            ).hexdigest(),
        )
        if len(ranked) < required:
            pair_rows.append(
                {
                    "event_id": event["event_id"],
                    "candidate_net_return": float(event["net_return"]),
                    "matched_control_count": len(ranked),
                    "match_status": "unmatched_insufficient_exact_stratum",
                    "control_mean_net_return": np.nan,
                    "paired_excess_return": np.nan,
                }
            )
            continue
        selected = ranked[:required]
        for rank, control_i in enumerate(selected):
            entry_i = control_i + 1
            entry_price = float(featured.loc[entry_i, "open"])
            risk_price = float(event["stop_distance_atr"]) * float(featured.loc[control_i, "atr"])
            result = resolve_path(
                featured,
                signal_i=control_i,
                entry_i=entry_i,
                direction=int(event["direction"]),
                entry_price=entry_price,
                risk_price=risk_price,
                config=config,
            )
            if not bool(result["resolved"]):
                raise RuntimeError("eligible matched control unexpectedly unresolved")
            control_rows.append(
                {
                    "candidate_event_id": event["event_id"],
                    "control_rank": rank,
                    "control_signal_i": control_i,
                    "control_signal_time": featured.loc[control_i, "open_time"],
                    "control_entry_time": featured.loc[entry_i, "open_time"],
                    "direction": int(event["direction"]),
                    "side": event["side"],
                    "month": key[0],
                    "utc_six_hour_block": key[1],
                    "atr_quintile": key[2],
                    "copied_stop_distance_atr": float(event["stop_distance_atr"]),
                    **result,
                }
            )
        current = [row for row in control_rows if row["candidate_event_id"] == event["event_id"]]
        pair_rows.append(
            {
                "event_id": event["event_id"],
                "candidate_net_return": float(event["net_return"]),
                "matched_control_count": required,
                "match_status": "matched_exact",
                "control_mean_net_return": float(np.mean([float(row["net_return"]) for row in current])),
                "paired_excess_return": float(event["net_return"])
                - float(np.mean([float(row["net_return"]) for row in current])),
            }
        )
    return pd.DataFrame(control_rows), pd.DataFrame(pair_rows)


def signflip_p(values: Iterable[float], *, resamples: int, seed: int) -> float:
    array = np.asarray(list(values), dtype=float)
    array = array[np.isfinite(array)]
    if not len(array):
        return float("nan")
    observed = float(array.mean())
    if len(array) <= 16:
        masks = np.arange(2 ** len(array), dtype=np.uint64)[:, None]
        bits = ((masks >> np.arange(len(array), dtype=np.uint64)) & 1).astype(float)
        signs = bits * 2.0 - 1.0
        null = (signs * array).mean(axis=1)
        return float((np.sum(null >= observed - 1e-15) + 1) / (len(null) + 1))
    rng = np.random.default_rng(seed)
    exceed = 0
    done = 0
    chunk = 10_000
    while done < resamples:
        current = min(chunk, resamples - done)
        signs = rng.choice(np.array([-1.0, 1.0]), size=(current, len(array)))
        exceed += int(np.sum((signs * array).mean(axis=1) >= observed - 1e-15))
        done += current
    return float((exceed + 1) / (resamples + 1))


def bootstrap_mean_ci(values: Iterable[float], *, resamples: int, seed: int) -> tuple[float, float]:
    array = np.asarray(list(values), dtype=float)
    array = array[np.isfinite(array)]
    if not len(array):
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    means = np.empty(resamples, dtype=float)
    for start in range(0, resamples, 2000):
        stop = min(resamples, start + 2000)
        sample = rng.integers(0, len(array), size=(stop - start, len(array)))
        means[start:stop] = array[sample].mean(axis=1)
    low, high = np.quantile(means, [0.025, 0.975])
    return float(low), float(high)


def summarize_returns(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "n": 0,
            "win_rate_net": None,
            "mean_gross_bp": None,
            "mean_net_bp": None,
            "median_net_bp": None,
            "profit_factor_net": None,
        }
    return {
        "n": len(frame),
        "win_rate_net": float(frame["net_return"].gt(0.0).mean()),
        "mean_gross_bp": float(frame["gross_return"].mean() * 10_000.0),
        "mean_net_bp": float(frame["net_return"].mean() * 10_000.0),
        "median_net_bp": float(frame["net_return"].median() * 10_000.0),
        "profit_factor_net": float(profit_factor(frame["net_return"])),
        "mean_return_r": float(frame["return_r"].mean()),
        "mean_net_return_r": float(frame["net_return_r"].mean()),
        "mean_hold_bars": float(frame["hold_bars"].mean()),
    }


def one_position_sensitivity(events: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    resolved = events[events["resolved"].astype(bool)].sort_values("entry_i")
    kept: list[dict[str, Any]] = []
    last_exit = -1
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for row in resolved.to_dict("records"):
        if int(row["entry_i"]) <= last_exit:
            continue
        equity *= 1.0 + float(row["net_return"])
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - 1.0)
        row["one_position_equity"] = equity
        row["one_position_drawdown"] = equity / peak - 1.0
        kept.append(row)
        last_exit = int(row["exit_i"])
    output = pd.DataFrame(kept)
    summary = {
        **summarize_returns(output),
        "compounded_return": equity - 1.0,
        "max_drawdown": max_drawdown,
        "signals_skipped_while_open": len(resolved) - len(output),
    }
    return output, summary


def equal_risk_sensitivity(events: pd.DataFrame, risk_fraction: float = 0.01) -> dict[str, Any]:
    """Size each sequential trade to the same equity risk using frozen net R."""

    resolved = events[events["resolved"].astype(bool)].sort_values("entry_i")
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for net_r in resolved["net_return_r"].astype(float):
        equity *= 1.0 + risk_fraction * net_r
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - 1.0)
    return {
        "posthoc_sensitivity": True,
        "risk_fraction_per_trade": risk_fraction,
        "n": len(resolved),
        "mean_net_return_r": float(resolved["net_return_r"].mean()),
        "sum_net_return_r": float(resolved["net_return_r"].sum()),
        "compounded_return": equity - 1.0,
        "max_drawdown": max_drawdown,
    }


def _holm_adjust(p_values: Iterable[float]) -> list[float]:
    values = np.asarray(list(p_values), dtype=float)
    output = np.full(len(values), np.nan, dtype=float)
    finite = np.flatnonzero(np.isfinite(values))
    order = finite[np.argsort(values[finite])]
    running = 0.0
    total = len(order)
    for rank, index in enumerate(order):
        running = max(running, float(values[index]) * (total - rank))
        output[index] = min(1.0, running)
    return output.tolist()


def _binary_mean_permutation_p(
    returns: np.ndarray,
    mask: np.ndarray,
    *,
    resamples: int,
    seed: int,
) -> float:
    if not mask.any() or mask.all():
        return float("nan")
    observed = float(returns[mask].mean() - returns[~mask].mean())
    rng = np.random.default_rng(seed)
    exceed = 0
    done = 0
    while done < resamples:
        current = min(5_000, resamples - done)
        shuffled = np.vstack([rng.permutation(returns) for _ in range(current)])
        differences = shuffled[:, mask].mean(axis=1) - shuffled[:, ~mask].mean(axis=1)
        exceed += int(np.sum(np.abs(differences) >= abs(observed) - 1e-15))
        done += current
    return float((exceed + 1) / (resamples + 1))


def factor_diagnostics(events: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Describe frozen pre-entry flags; inference is exploratory and multiplicity-corrected."""

    resolved = events[events["resolved"].astype(bool)].copy()
    rows: list[dict[str, Any]] = []
    returns = resolved["net_return"].to_numpy(dtype=float)
    wins = resolved["net_return"].gt(0.0).to_numpy(dtype=bool)
    for flag_index, (flag, description) in enumerate(config["diagnostics"]["causal_flags"].items()):
        present = (
            resolved["causal_flags"]
            .fillna("")
            .str.split("|")
            .apply(lambda values, current_flag=flag: current_flag in values)
        )
        present_array = present.to_numpy(dtype=bool)
        with_flag = resolved[present]
        without_flag = resolved[~present]
        table = [
            [int(np.sum(present_array & wins)), int(np.sum(present_array & ~wins))],
            [int(np.sum(~present_array & wins)), int(np.sum(~present_array & ~wins))],
        ]
        _, fisher_p = fisher_exact(table, alternative="two-sided")
        rows.append(
            {
                "flag": flag,
                "description": description,
                "n_with": len(with_flag),
                "n_without": len(without_flag),
                "win_rate_with": float(with_flag["net_return"].gt(0.0).mean()) if len(with_flag) else np.nan,
                "win_rate_without": float(without_flag["net_return"].gt(0.0).mean()) if len(without_flag) else np.nan,
                "win_rate_delta": (
                    float(with_flag["net_return"].gt(0.0).mean() - without_flag["net_return"].gt(0.0).mean())
                    if len(with_flag) and len(without_flag)
                    else np.nan
                ),
                "mean_net_bp_with": float(with_flag["net_return"].mean() * 10_000.0) if len(with_flag) else np.nan,
                "mean_net_bp_without": float(without_flag["net_return"].mean() * 10_000.0) if len(without_flag) else np.nan,
                "mean_net_bp_delta": (
                    float((with_flag["net_return"].mean() - without_flag["net_return"].mean()) * 10_000.0)
                    if len(with_flag) and len(without_flag)
                    else np.nan
                ),
                "win_rate_fisher_p_two_sided": float(fisher_p),
                "mean_net_permutation_p_two_sided": _binary_mean_permutation_p(
                    returns,
                    present_array,
                    resamples=int(config["evaluation"]["permutation_resamples"]),
                    seed=2026090410 + flag_index,
                ),
                "exploratory_only": True,
            }
        )
    output = pd.DataFrame(rows)
    output["win_rate_fisher_p_holm"] = _holm_adjust(output["win_rate_fisher_p_two_sided"])
    output["mean_net_permutation_p_holm"] = _holm_adjust(
        output["mean_net_permutation_p_two_sided"]
    )
    return output


def monthly_summary(events: pd.DataFrame, pairs: pd.DataFrame) -> pd.DataFrame:
    resolved = events[events["resolved"].astype(bool)].copy()
    if resolved.empty:
        return pd.DataFrame()
    resolved["month"] = resolved["entry_time"].dt.strftime("%Y-%m")
    paired = pairs.merge(resolved[["event_id", "month"]], on="event_id", how="left", validate="one_to_one")
    rows: list[dict[str, Any]] = []
    for month, group in resolved.groupby("month", sort=True):
        control = paired[paired["month"].eq(month)]
        rows.append(
            {
                "month": month,
                "n": len(group),
                "long": int(group["direction"].eq(1).sum()),
                "short": int(group["direction"].eq(-1).sum()),
                "tp": int(group["outcome"].eq("tp").sum()),
                "sl": int(group["outcome"].isin(["sl", "sl_ambiguous"]).sum()),
                "timeout": int(group["outcome"].eq("timeout").sum()),
                "win_rate_net": float(group["net_return"].gt(0.0).mean()),
                "mean_net_bp": float(group["net_return"].mean() * 10_000.0),
                "control_mean_net_bp": float(control["control_mean_net_return"].mean() * 10_000.0),
                "paired_excess_bp": float(control["paired_excess_return"].mean() * 10_000.0),
            }
        )
    return pd.DataFrame(rows)


def plot_overview(
    events: pd.DataFrame,
    pairs: pd.DataFrame,
    monthly: pd.DataFrame,
    one_position: pd.DataFrame,
    output: Path,
) -> None:
    resolved = events[events["resolved"].astype(bool)].copy()
    fig, axes = plt.subplots(2, 2, figsize=(15, 10.5))
    outcome_order = ["tp", "sl", "sl_ambiguous", "timeout"]
    counts = resolved["outcome"].value_counts().reindex(outcome_order, fill_value=0)
    axes[0, 0].bar(counts.index, counts.values, color=[TEAL, ORANGE, ORANGE, MUTED], edgecolor=INK)
    axes[0, 0].set_title("Resolved trade outcomes")
    axes[0, 0].set_ylabel("Trades")

    x = np.arange(len(monthly))
    axes[0, 1].bar(x - 0.18, monthly["mean_net_bp"], 0.36, label="signal", color=TEAL, edgecolor=INK)
    axes[0, 1].bar(
        x + 0.18,
        monthly["control_mean_net_bp"],
        0.36,
        label="matched control",
        color=LIGHT_ORANGE,
        edgecolor=ORANGE,
    )
    axes[0, 1].axhline(0.0, color=INK, linewidth=0.8)
    axes[0, 1].set_xticks(x, monthly["month"], rotation=30, ha="right")
    axes[0, 1].set_title("Monthly net return per signal")
    axes[0, 1].set_ylabel("Basis points")
    axes[0, 1].legend(frameon=False)

    if len(one_position):
        axes[1, 0].plot(
            one_position["entry_time"],
            one_position["one_position_equity"],
            color=TEAL,
            marker="o",
            markersize=3,
        )
    axes[1, 0].axhline(1.0, color=INK, linewidth=0.8, linestyle="--")
    axes[1, 0].set_title("One-position-at-a-time 1x equity sensitivity")
    axes[1, 0].set_ylabel("Equity, start = 1.0")

    axes[1, 1].scatter(
        resolved["stop_distance_atr"],
        resolved["net_return"] * 10_000.0,
        c=np.where(resolved["net_return"].gt(0.0), TEAL, ORANGE),
        edgecolor=INK,
        linewidth=0.4,
        alpha=0.85,
    )
    axes[1, 1].axhline(0.0, color=INK, linewidth=0.8)
    axes[1, 1].set_title("Signal risk distance and realized net return")
    axes[1, 1].set_xlabel("K2 stop distance / ATR14")
    axes[1, 1].set_ylabel("Net basis points")

    for axis in axes.flat:
        axis.grid(axis="y", color=GRID, linewidth=0.65)
        axis.spines[["top", "right"]].set_visible(False)
    fig.suptitle(
        "BTCUSDT.P · Pine v8 Core Recall · 1h · 2026-03-04 to 2026-09-04",
        x=0.02,
        ha="left",
        fontsize=16,
        color=INK,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(output, dpi=180, facecolor="white")
    plt.close(fig)


def plot_factor_diagnostics(factors: pd.DataFrame, output: Path) -> None:
    current = factors.dropna(subset=["mean_net_bp_delta"]).copy()
    current = current[current["n_with"].ge(2) & current["n_without"].ge(2)]
    current = current.sort_values("mean_net_bp_delta")
    fig_height = max(6.5, len(current) * 0.38)
    fig, axis = plt.subplots(figsize=(13, fig_height))
    colours = [ORANGE if value < 0 else TEAL for value in current["mean_net_bp_delta"]]
    axis.barh(current["flag"], current["mean_net_bp_delta"], color=colours, edgecolor=INK)
    axis.axvline(0.0, color=INK, linewidth=0.9)
    axis.set_title("Pre-entry flag association with net return (exploratory)")
    axis.set_xlabel("Mean net bp with flag minus without flag")
    axis.grid(axis="x", color=GRID, linewidth=0.65)
    axis.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(output, dpi=180, facecolor="white")
    plt.close(fig)


def plot_reason_diagnostics(events: pd.DataFrame, output: Path) -> None:
    """Show realized failure modes and the cost/sizing sensitivity."""

    resolved = events[events["resolved"].astype(bool)].sort_values("entry_i").copy()
    order = [
        "fast_clean_tp",
        "ordinary_tp",
        "timeout_gain",
        "timeout_loss",
        "immediate_reversal_sl",
        "giveback_then_sl",
        "ordinary_sl",
    ]
    labels = {
        "fast_clean_tp": "fast clean TP",
        "ordinary_tp": "ordinary TP",
        "timeout_gain": "timeout gain",
        "timeout_loss": "timeout loss",
        "immediate_reversal_sl": "immediate reversal SL",
        "giveback_then_sl": "giveback then SL",
        "ordinary_sl": "ordinary SL",
    }
    counts = resolved["path_class"].value_counts().reindex(order, fill_value=0)
    fig, axes = plt.subplots(1, 2, figsize=(15, 6.5))
    colours = [TEAL if "tp" in key or "gain" in key else ORANGE for key in order]
    axes[0].barh([labels[key] for key in order], counts.values, color=colours, edgecolor=INK)
    axes[0].invert_yaxis()
    axes[0].set_title("Why trades resolved as success or failure")
    axes[0].set_xlabel("Trades")
    for index, value in enumerate(counts.values):
        axes[0].text(value + 0.15, index, str(int(value)), va="center", color=INK)

    trade_number = np.arange(len(resolved) + 1)
    gross_equity = np.r_[1.0, np.cumprod(1.0 + resolved["gross_return"].to_numpy(dtype=float))]
    net_equity = np.r_[1.0, np.cumprod(1.0 + resolved["net_return"].to_numpy(dtype=float))]
    risk_equity = np.r_[1.0, np.cumprod(1.0 + 0.01 * resolved["net_return_r"].to_numpy(dtype=float))]
    axes[1].plot(trade_number, gross_equity, color=TEAL, linewidth=2.0, label="equal notional, zero cost")
    axes[1].plot(trade_number, net_equity, color=BLUE, linewidth=2.0, label="equal notional, cost included")
    axes[1].plot(trade_number, risk_equity, color=ORANGE, linewidth=2.0, label="equal 1% risk, cost included")
    axes[1].axhline(1.0, color=INK, linewidth=0.8, linestyle="--")
    axes[1].set_title("Cost and position-sizing sensitivity")
    axes[1].set_xlabel("Chronological resolved trades")
    axes[1].set_ylabel("Equity, start = 1.0")
    axes[1].legend(frameon=False)

    for axis in axes:
        axis.grid(axis="y", color=GRID, linewidth=0.65)
        axis.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(output, dpi=180, facecolor="white")
    plt.close(fig)


def _plot_trade_panel(axis: plt.Axes, featured: pd.DataFrame, trade: dict[str, Any], rank: int) -> None:
    k1_i = int(trade["k1_i"])
    k2_i = int(trade["k2_i"])
    right_anchor = int(trade["exit_i"]) if bool(trade["resolved"]) else min(len(featured) - 1, int(trade["entry_i"]) + 11)
    left = max(0, k1_i - 8)
    right = min(len(featured) - 1, max(right_anchor + 2, k2_i + 4))
    data = featured.iloc[left : right + 1].copy()
    x = np.arange(len(data))
    for local, (_, row) in enumerate(data.iterrows()):
        colour = TEAL if (float(row["high"]) + float(row["low"])) / 2.0 >= float(row["sma40_hl2"]) else ORANGE
        axis.vlines(local, row["low"], row["high"], color=colour, linewidth=0.85, zorder=3)
        body_low = min(float(row["open"]), float(row["close"]))
        body_height = max(abs(float(row["close"]) - float(row["open"])), 0.4)
        axis.add_patch(
            Rectangle(
                (local - 0.30, body_low),
                0.60,
                body_height,
                facecolor=colour,
                edgecolor=colour,
                linewidth=0.65,
                zorder=4,
            )
        )
    axis.plot(x, data["sma40_hl2"], color=BLUE, linewidth=1.15, zorder=2)
    for global_i, label in ((k1_i, "K1"), (k2_i, "K2")):
        local_i = global_i - left
        row = featured.loc[global_i]
        axis.scatter(local_i, row["low"] if int(trade["direction"]) > 0 else row["high"], s=42, color=INK, zorder=6)
        axis.text(local_i, row["high"], label, ha="center", va="bottom", fontsize=8, fontweight="bold")
    entry_local = int(trade["entry_i"]) - left
    axis.scatter(entry_local, trade["entry_price"], marker=">", s=55, color=BLUE, edgecolor=INK, linewidth=0.4, zorder=7)
    axis.axhline(trade["entry_price"], color=MUTED, linewidth=0.8, linestyle="--")
    axis.axhline(trade["stop_price"], color=ORANGE, linewidth=1.0)
    axis.axhline(trade["target_price"], color=TEAL, linewidth=1.0)
    axis.axvspan(entry_local - 0.45, min(len(data) - 0.5, entry_local + 11.55), color=LIGHT_TEAL, alpha=0.22)
    outcome = str(trade["outcome"]).upper()
    net = "open" if not bool(trade["resolved"]) else f"{float(trade['net_return']) * 100:+.2f}%"
    local_time = _utc(trade["entry_time"]).tz_convert("Asia/Shanghai").strftime("%m-%d %H:%M")
    axis.set_title(
        f"#{rank:02d} {str(trade['side']).upper()} · {outcome} · {net} · {local_time} CST\n"
        f"gap={int(trade['gap_bars'])} risk={float(trade['stop_distance_atr']):.2f}ATR · {trade['path_class']}",
        loc="left",
        fontsize=9,
        color=INK,
    )
    step = max(1, len(data) // 6)
    ticks = x[::step]
    labels = [stamp.tz_convert("Asia/Shanghai").strftime("%m-%d\n%H:%M") for stamp in data["open_time"].iloc[::step]]
    axis.set_xticks(ticks, labels, fontsize=7)
    axis.grid(axis="y", color=GRID, linewidth=0.55)
    axis.spines[["top", "right"]].set_visible(False)


def plot_trade_pages(events: pd.DataFrame, featured: pd.DataFrame, directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    pages: list[Path] = []
    records = events.sort_values("entry_i").to_dict("records")
    for page_index, start in enumerate(range(0, len(records), 4), start=1):
        subset = records[start : start + 4]
        fig, axes = plt.subplots(2, 2, figsize=(16, 10.5))
        for local, axis in enumerate(axes.flat):
            if local >= len(subset):
                axis.axis("off")
                continue
            _plot_trade_panel(axis, featured, subset[local], start + local + 1)
        fig.suptitle(
            f"BTCUSDT.P 1h Pine-v8 trade audit · page {page_index}",
            x=0.02,
            ha="left",
            fontsize=15,
            color=INK,
        )
        fig.tight_layout(rect=(0, 0, 1, 0.96))
        path = directory / f"trade_audit_page_{page_index:02d}.png"
        fig.savefig(path, dpi=170, facecolor="white")
        plt.close(fig)
        pages.append(path)
    return pages


def run(config: dict[str, Any], source_path: Path) -> dict[str, Any]:
    """Execute the frozen analysis and persist auditable ledgers."""

    expected_pine_hash = str(config["signal"]["pine_source_sha256"])
    actual_pine_hash = sha256_file(PINE_PATH)
    if actual_pine_hash != expected_pine_hash:
        raise RuntimeError(f"Pine source drift: expected {expected_pine_hash}, got {actual_pine_hash}")
    raw, quality = load_hourly_source(source_path, config)
    crosschecks = crosscheck_sources(raw)
    featured = add_features(raw)
    candidates = detect_raw_candidates(featured, config)
    accepted_all = accept_pine_events(candidates, featured, config)
    analysis_start = _utc(config["window"]["analysis_start_inclusive"])
    snapshot_end = _utc(config["window"]["snapshot_end_exclusive"])
    events = accepted_all[
        accepted_all["entry_time"].ge(analysis_start)
        & accepted_all["entry_time"].lt(snapshot_end)
    ].reset_index(drop=True)
    events = attach_outcomes(events, featured, config)
    controls, pairs = build_matched_controls(events, featured, config)
    monthly = monthly_summary(events, pairs)
    factors = factor_diagnostics(events, config)
    one_position, one_position_summary = one_position_sensitivity(events)
    equal_risk_summary = equal_risk_sensitivity(events)
    resolved = events[events["resolved"].astype(bool)].copy()
    paired_differences = (
        pairs["paired_excess_return"].dropna().to_numpy(dtype=float)
        if len(pairs)
        else np.array([])
    )
    ci_low, ci_high = bootstrap_mean_ci(
        resolved["net_return"],
        resamples=int(config["evaluation"]["bootstrap_resamples"]),
        seed=2026090401,
    )
    control_summary = summarize_returns(controls)
    terminal_month_start = snapshot_end.normalize().replace(day=1)
    before_terminal_month = resolved[resolved["entry_time"].lt(terminal_month_start)].copy()
    matched_event_ids = set(pairs.loc[pairs["match_status"].eq("matched_exact"), "event_id"])
    matched_candidates = resolved[resolved["event_id"].isin(matched_event_ids)].copy()
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "experiment_id": config["experiment_id"],
        "configuration_holdout_use": int(config["owner_authorization"]["configuration_holdout_use"]),
        "owner_holdout_authorization": config["owner_authorization"]["verbatim"],
        "instrument": config["instrument"],
        "window": config["window"],
        "config_sha256": sha256_file(CONFIG_PATH),
        "pine_source_sha256": actual_pine_hash,
        "source_sha256": sha256_file(source_path),
        "source_quality": quality,
        "source_crosschecks": crosschecks,
        "raw_best_k1_candidates_all_warmup": len(candidates),
        "accepted_events_all_warmup": len(accepted_all),
        "analysis_signals": len(events),
        "resolved_signals": len(resolved),
        "unresolved_signals": int((~events["resolved"].astype(bool)).sum()),
        "direction_counts": {str(key): int(value) for key, value in events["side"].value_counts().items()},
        "outcome_counts": {str(key): int(value) for key, value in events["outcome"].value_counts().items()},
        "path_class_counts": {str(key): int(value) for key, value in events["path_class"].value_counts().items()},
        "primary_every_signal": summarize_returns(resolved),
        "primary_additive_net_return": float(resolved["net_return"].sum()),
        "primary_equal_notional_compounded_return": float(np.prod(1.0 + resolved["net_return"]) - 1.0),
        "zero_cost_equal_notional_compounded_return": float(np.prod(1.0 + resolved["gross_return"]) - 1.0),
        "primary_mean_net_signflip_p_one_sided": signflip_p(
            resolved["net_return"],
            resamples=int(config["evaluation"]["permutation_resamples"]),
            seed=2026090404,
        ),
        "primary_mean_net_bp_bootstrap_95_ci": [ci_low * 10_000.0, ci_high * 10_000.0],
        "excluding_terminal_partial_month": {
            **summarize_returns(before_terminal_month),
            "end_exclusive": terminal_month_start,
            "mean_net_signflip_p_one_sided": signflip_p(
                before_terminal_month["net_return"],
                resamples=int(config["evaluation"]["permutation_resamples"]),
                seed=2026090405,
            ),
        },
        "matched_controls": control_summary,
        "matched_candidates": summarize_returns(matched_candidates),
        "matched_pair_count": int(pairs["match_status"].eq("matched_exact").sum()),
        "unmatched_pair_count": int(pairs["match_status"].ne("matched_exact").sum()),
        "unmatched_event_ids": pairs.loc[
            pairs["match_status"].ne("matched_exact"), "event_id"
        ].tolist(),
        "candidate_minus_control_mean_bp": float(paired_differences.mean() * 10_000.0) if len(paired_differences) else None,
        "paired_signflip_p_one_sided": signflip_p(
            paired_differences,
            resamples=int(config["evaluation"]["permutation_resamples"]),
            seed=2026090402,
        ),
        "one_position_sensitivity": one_position_summary,
        "equal_risk_1pct_sensitivity": equal_risk_summary,
        "auc": None,
        "auc_reason": config["evaluation"]["auc"],
        "top_decile": None,
        "top_decile_reason": config["evaluation"]["top_decile"],
        "funding_included": False,
        "thresholds_tuned_after_holdout": False,
        "training_eligible": False,
        "production_eligible": False,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    events.to_csv(RESULTS / "trade_ledger.csv", index=False)
    controls.to_csv(RESULTS / "matched_controls.csv", index=False)
    pairs.to_csv(RESULTS / "matched_pairs.csv", index=False)
    monthly.to_csv(RESULTS / "monthly_summary.csv", index=False)
    factors.to_csv(RESULTS / "causal_flag_diagnostics.csv", index=False)
    one_position.to_csv(RESULTS / "one_position_trades.csv", index=False)
    write_json(RESULTS / "data_quality.json", {"primary": quality, "crosschecks": crosschecks})
    write_json(RESULTS / "summary.json", summary)
    plot_overview(events, pairs, monthly, one_position, RESULTS / "overview.png")
    plot_factor_diagnostics(factors, RESULTS / "causal_flag_diagnostics.png")
    plot_reason_diagnostics(events, RESULTS / "reason_diagnostics.png")
    pages = plot_trade_pages(events, featured, RESULTS / "trade_pages")
    summary["trade_page_paths"] = [path.relative_to(PROJECT) for path in pages]
    write_json(RESULTS / "summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="freeze the official OKX snapshot before replay")
    parser.add_argument("--source", type=Path, default=SOURCE_PATH)
    args = parser.parse_args()
    config = load_config()
    RESULTS.mkdir(parents=True, exist_ok=True)
    if args.fetch:
        fetch_official_hourly(config, args.source)
    if not args.source.is_file():
        raise FileNotFoundError(f"missing frozen source {args.source}; run with --fetch")
    summary = run(config, args.source)
    print(json.dumps(_json_value(summary), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
