#!/usr/bin/env python3
"""Research an ETHUSDT.P-specific causal launch system across intraday bars.

Source columns are ``open_time/open/high/low/close/volume`` from official OKX
monthly one-minute archives aggregated to complete five-minute candles.  The
same five-minute bytes are causally aggregated to 15m, 30m, 1h, 2h and 4h.
Every signal and model feature at decision bar ``t`` reads only completed bars
through ``t``.  Entry is at ``open[t+1]``.  Only the trade resolver reads the
future path, bounded by the registered 24h or 96h horizon.

The discovery phase changes one registered categorical factor at a time:
trigger SMA/EMA, launch family, then exit policy.  Confirmation fits fixed
LightGBM return/runner-arm variants on 2023 and selects on 2024.  Audit refits
the nominated contract on 2023-2024 and replays 2025 through 2026-02 once.
The repository holdout begins 2026-05-04 and is physically excluded.  This
script never changes ACTIVE, forward logs, TradingView, deployment, sizing, or
a live account.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from scripts.research_btcusdtp_15m_ma_state_trend import (
    add_reference_features,
    build_raw_candidates,
)
from scripts.research_two_key_candle_ma_retest_1h import pine_rma, profit_factor


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-ethusdtp-multitimeframe-trend-confluence-preholdout-20260904-v1"
EXPERIMENT = ROOT / "experiments" / "active" / EXPERIMENT_ID
CONFIG_PATH = EXPERIMENT / "config.json"
RESULTS = EXPERIMENT / "results"
SELECTION_PATH = RESULTS / "discovery_selection_receipt.json"
CONFIRMATION_PATH = RESULTS / "confirmation_nomination_receipt.json"
SCRIPT_PATH = Path(__file__).resolve()
BASE_MINUTES = 5


def utc(value: object) -> pd.Timestamp:
    """Return a timezone-aware UTC timestamp."""

    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def json_value(value: Any) -> Any:
    """Convert pandas/numpy values to stable JSON-compatible values."""

    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, pd.Timestamp):
        return utc(value).isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(json_value(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".gz":
        frame.to_csv(path, index=False, compression={"method": "gzip", "mtime": 0})
    else:
        frame.to_csv(path, index=False)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_config() -> dict[str, Any]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if config["eligibility"]["holdout_consumed"]:
        raise RuntimeError("holdout-contaminated configuration cannot run this pipeline")
    if int(config["time_contract"]["holdout_price_rows_allowed"]) != 0:
        raise RuntimeError("this experiment must keep holdout_price_rows_allowed=0")
    return config


def locate_eth_source(config: Mapping[str, Any]) -> Path:
    hits = sorted(ROOT.glob(str(config["sources"]["eth"]["glob"])))
    if len(hits) != 1:
        raise RuntimeError(f"expected exactly one ETH 5m archive output, found {len(hits)}")
    return hits[0]


def _validate_ohlcv(frame: pd.DataFrame, *, delta: pd.Timedelta) -> dict[str, Any]:
    required = {"open_time", "open", "high", "low", "close", "volume"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"OHLCV source missing columns: {missing}")
    times = pd.to_datetime(frame["open_time"], utc=True, errors="raise")
    numeric = frame[["open", "high", "low", "close", "volume"]].apply(
        pd.to_numeric, errors="raise"
    )
    result = {
        "rows": int(len(frame)),
        "first_time": times.iloc[0].isoformat() if len(times) else None,
        "last_time": times.iloc[-1].isoformat() if len(times) else None,
        "duplicate_timestamps": int(times.duplicated().sum()),
        "nonpositive_ohlc": int((numeric[["open", "high", "low", "close"]] <= 0).sum().sum()),
        "negative_volume": int((numeric["volume"] < 0).sum()),
        "body_violations": int(
            (numeric["high"] < numeric[["open", "close"]].max(axis=1)).sum()
            + (numeric["low"] > numeric[["open", "close"]].min(axis=1)).sum()
        ),
        "non_contiguous_deltas": int((times.diff().dropna() != delta).sum()),
    }
    hard_fail = (
        result["duplicate_timestamps"]
        + result["nonpositive_ohlc"]
        + result["negative_volume"]
        + result["body_violations"]
    )
    if hard_fail:
        raise RuntimeError(f"OHLCV quality failure: {result}")
    return result


def load_native_5m(path: Path, config: Mapping[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load only the registered pre-holdout physical prefix."""

    raw = pd.read_csv(path)
    raw["open_time"] = pd.to_datetime(raw["open_time"], utc=True, errors="raise")
    for column in ("open", "high", "low", "close", "volume"):
        raw[column] = pd.to_numeric(raw[column], errors="raise")
    start = utc(config["time_contract"]["physical_start_inclusive"])
    end = utc(config["time_contract"]["audit_end_exclusive"])
    holdout = utc(config["time_contract"]["holdout_start"])
    raw = raw.loc[(raw["open_time"] >= start) & (raw["open_time"] < end)].copy()
    raw = raw.sort_values("open_time", kind="mergesort").drop_duplicates(
        "open_time", keep="last"
    ).reset_index(drop=True)
    quality = _validate_ohlcv(raw, delta=pd.Timedelta(minutes=BASE_MINUTES))
    quality.update(
        {
            "path": str(path.relative_to(ROOT)),
            "sha256": sha256_file(path),
            "bounded_end_exclusive": end.isoformat(),
            "holdout_start": holdout.isoformat(),
            "holdout_rows_read": int((raw["open_time"] >= holdout).sum()),
        }
    )
    if quality["holdout_rows_read"]:
        raise RuntimeError("physical loader crossed repository holdout")
    return raw, quality


def _add_atr_and_segments(frame: pd.DataFrame, minutes: int, atr_length: int) -> pd.DataFrame:
    out = frame.copy().reset_index(drop=True)
    delta = pd.Timedelta(minutes=minutes)
    out["segment_id"] = out["open_time"].diff().ne(delta).cumsum().astype(int)
    atr = np.full(len(out), np.nan, dtype=float)
    for _, segment in out.groupby("segment_id", sort=True):
        previous = segment["close"].shift(1)
        true_range = np.maximum(
            segment["high"] - segment["low"],
            np.maximum((segment["high"] - previous).abs(), (segment["low"] - previous).abs()),
        )
        atr[segment.index.to_numpy(dtype=int)] = pine_rma(
            true_range.to_numpy(dtype=float), atr_length
        )
    out["atr"] = atr
    return out


def aggregate_timeframe(native: pd.DataFrame, *, minutes: int, atr_length: int) -> pd.DataFrame:
    """Aggregate only complete contiguous UTC-aligned groups of native 5m bars."""

    if minutes < BASE_MINUTES or minutes % BASE_MINUTES:
        raise ValueError("timeframe minutes must be a multiple of five")
    if minutes == BASE_MINUTES:
        return _add_atr_and_segments(native.copy(), minutes, atr_length)
    expected = minutes // BASE_MINUTES
    source = native.copy()
    source["bucket"] = source["open_time"].dt.floor(f"{minutes}min")
    grouped = source.groupby("bucket", sort=True)
    result = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        count=("open_time", "size"),
        first_source=("open_time", "min"),
        last_source=("open_time", "max"),
    ).reset_index(names="open_time")
    complete = (
        result["count"].eq(expected)
        & result["first_source"].eq(result["open_time"])
        & result["last_source"].eq(
            result["open_time"] + pd.Timedelta(minutes=minutes - BASE_MINUTES)
        )
    )
    result = result.loc[complete, ["open_time", "open", "high", "low", "close", "volume"]]
    return _add_atr_and_segments(result.reset_index(drop=True), minutes, atr_length)


def load_frames(config: Mapping[str, Any]) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame], dict[str, Any]]:
    eth_path = locate_eth_source(config)
    btc_path = ROOT / str(config["sources"]["btc_resonance"]["path"])
    if sha256_file(btc_path) != str(config["sources"]["btc_resonance"]["sha256"]):
        raise RuntimeError("BTC resonance source SHA drift")
    eth, eth_quality = load_native_5m(eth_path, config)
    btc, btc_quality = load_native_5m(btc_path, config)
    atr_length = int(config["signal"]["atr_length"])
    eth_frames: dict[str, pd.DataFrame] = {}
    btc_frames: dict[str, pd.DataFrame] = {}
    timeframe_quality: dict[str, Any] = {}
    for spec in config["timeframes"]:
        timeframe = str(spec["id"])
        minutes = int(spec["minutes"])
        eth_frame = aggregate_timeframe(eth, minutes=minutes, atr_length=atr_length)
        btc_frame = aggregate_timeframe(btc, minutes=minutes, atr_length=atr_length)
        eth_frames[timeframe] = eth_frame
        btc_frames[timeframe] = btc_frame
        timeframe_quality[timeframe] = {
            "minutes": minutes,
            "eth_rows": len(eth_frame),
            "btc_rows": len(btc_frame),
            "eth_first": eth_frame["open_time"].iloc[0],
            "eth_last": eth_frame["open_time"].iloc[-1],
            "eth_segments": int(eth_frame["segment_id"].nunique()),
            "btc_segments": int(btc_frame["segment_id"].nunique()),
        }
    return eth_frames, btc_frames, {
        "eth": eth_quality,
        "btc": btc_quality,
        "timeframes": timeframe_quality,
        "holdout_rows_read": 0,
    }


def _reference(frame: pd.DataFrame, label: str) -> pd.Series:
    kind = label[:3]
    period = int(label[3:])
    hl2 = (frame["high"] + frame["low"]) / 2.0
    if kind == "SMA":
        return hl2.rolling(period, min_periods=period).mean()
    if kind == "EMA":
        return hl2.ewm(span=period, adjust=False, min_periods=period).mean()
    raise ValueError(f"unsupported reference {label}")


def add_runner_references(frame: pd.DataFrame) -> pd.DataFrame:
    """Add causal runner references within contiguous segments."""

    parts: list[pd.DataFrame] = []
    for _, segment in frame.groupby("segment_id", sort=True):
        current = segment.copy()
        current["runner_sma60"] = _reference(current, "SMA60")
        current["runner_ema60"] = _reference(current, "EMA60")
        current["runner_sma120"] = _reference(current, "SMA120")
        parts.append(current)
    return pd.concat(parts, ignore_index=True) if parts else frame.copy()


def build_k1k2_candidates(frame: pd.DataFrame, signal: Mapping[str, Any]) -> pd.DataFrame:
    """Enumerate exact K1-cross then K2-wick-retest episodes using no future bars."""

    cfg = signal["k1k2"]
    open_ = frame["open"].to_numpy(dtype=float)
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    atr = frame["atr"].to_numpy(dtype=float)
    ma = frame["reference_ma"].to_numpy(dtype=float)
    segment = frame["segment_id"].to_numpy(dtype=int)
    bar_range = high - low
    body_low = np.minimum(open_, close)
    body_high = np.maximum(open_, close)
    body_ratio = np.abs(close - open_) / np.where(bar_range > 0.0, bar_range, np.nan)
    range_atr = bar_range / np.where(atr > 0.0, atr, np.nan)
    rows: list[dict[str, Any]] = []
    for direction in (1, -1):
        if direction > 0:
            k1_cross = (open_ <= ma) & (close > ma) & (close > open_)
            k1_close_location = (close - low) / bar_range
        else:
            k1_cross = (open_ >= ma) & (close < ma) & (close < open_)
            k1_close_location = (high - close) / bar_range
        k1_ok = (
            k1_cross
            & (body_ratio >= float(cfg["k1_body_ratio_min"]))
            & (range_atr >= float(cfg["k1_range_atr_min"]))
            & (k1_close_location >= float(cfg["k1_directional_close_location_min"]))
        )
        for k1_i in np.flatnonzero(k1_ok):
            for gap in range(int(cfg["gap_bars_min"]), int(cfg["gap_bars_max"]) + 1):
                k2_i = int(k1_i + gap)
                if k2_i >= len(frame) or segment[k2_i] != segment[k1_i]:
                    break
                between = np.arange(k1_i + 1, k2_i, dtype=int)
                if len(between):
                    correct = direction * (close[between] - ma[between]) >= 0.0
                    path_share = float(np.mean(correct))
                else:
                    path_share = 1.0
                if path_share < float(cfg["path_correct_side_share_min"]):
                    continue
                if direction > 0:
                    physical_touch = low[k2_i] <= ma[k2_i]
                    body_side = body_low[k2_i] >= ma[k2_i]
                    wick_share = (body_low[k2_i] - low[k2_i]) / bar_range[k2_i]
                    rejection_close = (close[k2_i] - low[k2_i]) / bar_range[k2_i]
                    touch_depth = (ma[k2_i] - low[k2_i]) / atr[k2_i]
                    signed_close = (close[k2_i] - ma[k2_i]) / atr[k2_i]
                else:
                    physical_touch = high[k2_i] >= ma[k2_i]
                    body_side = body_high[k2_i] <= ma[k2_i]
                    wick_share = (high[k2_i] - body_high[k2_i]) / bar_range[k2_i]
                    rejection_close = (high[k2_i] - close[k2_i]) / bar_range[k2_i]
                    touch_depth = (high[k2_i] - ma[k2_i]) / atr[k2_i]
                    signed_close = (ma[k2_i] - close[k2_i]) / atr[k2_i]
                finite = np.isfinite(
                    [wick_share, rejection_close, touch_depth, signed_close, body_ratio[k2_i]]
                ).all()
                if not (
                    finite
                    and physical_touch
                    and body_side
                    and wick_share >= float(cfg["k2_wick_share_min"])
                    and body_ratio[k2_i] <= float(cfg["k2_body_ratio_max"])
                    and rejection_close >= float(cfg["k2_directional_close_location_min"])
                ):
                    continue
                score = float(
                    np.mean(
                        [
                            np.clip(body_ratio[k1_i] / 0.8, 0.0, 1.0),
                            np.clip(range_atr[k1_i] / 1.5, 0.0, 1.0),
                            np.clip(k1_close_location[k1_i], 0.0, 1.0),
                            np.clip(wick_share / 0.5, 0.0, 1.0),
                            np.clip(rejection_close, 0.0, 1.0),
                            path_share,
                        ]
                    )
                )
                rows.append(
                    {
                        "signal_i": k2_i,
                        "direction": direction,
                        "signal_family": "k1k2",
                        "signal_score": score,
                        "signal_time": frame.loc[k2_i, "open_time"],
                        "signal_atr": float(atr[k2_i]),
                        "signal_ma": float(ma[k2_i]),
                        "signed_close_atr": float(signed_close),
                        "signed_slope_atr_per_bar": float(
                            direction * frame.loc[k2_i, "reference_slope_atr_per_bar"]
                        ),
                        "signed_body_atr": float(direction * (close[k2_i] - open_[k2_i]) / atr[k2_i]),
                        "breakout_atr": np.nan,
                        "prior_near_ma_share": float(frame.loc[k2_i, "prior_near_ma_share"]),
                        "prior_range_atr": float(frame.loc[k2_i, "prior_range_atr"]),
                        "k1_i": int(k1_i),
                        "gap_bars": int(gap),
                        "k1_body_ratio": float(body_ratio[k1_i]),
                        "k1_range_atr": float(range_atr[k1_i]),
                        "k1_close_location": float(k1_close_location[k1_i]),
                        "k2_wick_share": float(wick_share),
                        "k2_body_ratio": float(body_ratio[k2_i]),
                        "k2_rejection_close_location": float(rejection_close),
                        "k2_touch_depth_atr": float(touch_depth),
                        "path_correct_side_share": path_share,
                    }
                )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(
        ["signal_i", "signal_score", "direction"],
        ascending=[True, False, False],
        kind="mergesort",
    ).drop_duplicates(["signal_i", "direction"], keep="first").reset_index(drop=True)


def build_candidates(
    frame: pd.DataFrame, signal: Mapping[str, Any], family: str
) -> pd.DataFrame:
    state_map = {
        "direct": "direct",
        "rejection": "direct_rejection",
        "coil": "direct_coil",
        "direct_rejection": "direct_rejection",
        "all_state": "all",
        "all_with_k1k2": "all",
    }
    parts: list[pd.DataFrame] = []
    if family in state_map:
        state = build_raw_candidates(frame, signal, state_map[family])
        if family == "rejection" and len(state):
            state = state[state["signal_family"].astype(str).str.contains("rejection")]
        if family == "coil" and len(state):
            state = state[state["signal_family"].astype(str).str.contains("coil")]
        if len(state):
            parts.append(state)
    if family in {"k1k2", "all_with_k1k2"}:
        exact = build_k1k2_candidates(frame, signal)
        if len(exact):
            parts.append(exact)
    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts, ignore_index=True, sort=False)
    return out.sort_values(
        ["signal_i", "signal_score", "direction"],
        ascending=[True, False, False],
        kind="mergesort",
    ).reset_index(drop=True)


def accept_candidates(
    candidates: pd.DataFrame,
    frame: pd.DataFrame,
    *,
    timeframe: str,
    minutes: int,
    cooldown_bars: int,
) -> pd.DataFrame:
    """Apply one global episode cooldown and next-open availability."""

    if candidates.empty:
        return candidates.copy()
    delta = pd.Timedelta(minutes=minutes)
    accepted: list[dict[str, Any]] = []
    last_signal = -10**12
    for signal_i, group in candidates.groupby("signal_i", sort=True):
        signal_i = int(signal_i)
        entry_i = signal_i + 1
        if entry_i >= len(frame) or signal_i - last_signal < cooldown_bars:
            continue
        if (
            int(frame.loc[entry_i, "segment_id"]) != int(frame.loc[signal_i, "segment_id"])
            or frame.loc[entry_i, "open_time"] - frame.loc[signal_i, "open_time"] != delta
        ):
            continue
        row = group.sort_values(
            ["signal_score", "direction"], ascending=[False, False], kind="mergesort"
        ).iloc[0].to_dict()
        direction = int(row["direction"])
        signal_time = utc(row["signal_time"])
        identity = f"ETH-USDT-SWAP|{timeframe}|{direction}|{signal_time.isoformat()}|{row['signal_family']}"
        row.update(
            {
                "setup_id": hashlib.sha256(identity.encode()).hexdigest()[:20],
                "timeframe": timeframe,
                "bar_minutes": minutes,
                "entry_i": entry_i,
                "entry_time": frame.loc[entry_i, "open_time"],
                "entry_price": float(frame.loc[entry_i, "open"]),
            }
        )
        accepted.append(row)
        last_signal = signal_i
    return pd.DataFrame(accepted).reset_index(drop=True)


def parse_exit_policy(policy: str) -> dict[str, Any]:
    mapping = {
        "fixed_5atr_h24": {"kind": "fixed", "horizon_hours": 24, "runner": None, "buffer": None},
        "runner_sma60_b1_h24": {"kind": "runner", "horizon_hours": 24, "runner": "runner_sma60", "buffer": 1.0},
        "runner_sma60_b1_h96": {"kind": "runner", "horizon_hours": 96, "runner": "runner_sma60", "buffer": 1.0},
        "runner_ema60_b1_h96": {"kind": "runner", "horizon_hours": 96, "runner": "runner_ema60", "buffer": 1.0},
        "runner_sma120_b1_h96": {"kind": "runner", "horizon_hours": 96, "runner": "runner_sma120", "buffer": 1.0},
        "runner_sma60_b0_5_h96": {"kind": "runner", "horizon_hours": 96, "runner": "runner_sma60", "buffer": 0.5},
    }
    if policy not in mapping:
        raise ValueError(f"unknown exit policy: {policy}")
    return mapping[policy]


def _stop_fill(open_price: float, active_stop: float, direction: int) -> float:
    if direction > 0 and open_price < active_stop:
        return open_price
    if direction < 0 and open_price > active_stop:
        return open_price
    return active_stop


def resolve_trade(
    frame: pd.DataFrame,
    event: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    policy: str,
) -> dict[str, Any]:
    """Resolve a registered trade; runner updates are effective one bar later."""

    spec = parse_exit_policy(policy)
    minutes = int(event["bar_minutes"])
    horizon = int(spec["horizon_hours"] * 60 // minutes)
    entry_i = int(event["entry_i"])
    end_i = min(entry_i + horizon - 1, len(frame) - 1)
    if int(frame.loc[entry_i, "segment_id"]) != int(frame.loc[end_i, "segment_id"]):
        return {"resolved": False, "reason": "horizon_crosses_gap"}
    execution = config["execution"]
    direction = int(event["direction"])
    entry = float(event["entry_price"])
    signal_atr = float(event["signal_atr"])
    hard_stop = entry - direction * float(execution["initial_disaster_stop_atr"]) * signal_atr
    target = entry + direction * float(execution["fixed_target_atr"]) * signal_atr
    active_stop = hard_stop
    runner_armed = False
    armed_i: int | None = None
    exit_i: int | None = None
    exit_price: float | None = None
    outcome = ""
    stop_source = "hard"
    exit_mfe = 0.0
    exit_mae = 0.0
    full_mfe = 0.0
    full_mae = 0.0
    maximum_close_profit = -np.inf
    for i in range(entry_i, end_i + 1):
        open_price = float(frame.loc[i, "open"])
        high = float(frame.loc[i, "high"])
        low = float(frame.loc[i, "low"])
        close = float(frame.loc[i, "close"])
        favourable = high - entry if direction > 0 else entry - low
        adverse = entry - low if direction > 0 else high - entry
        close_profit = direction * (close - entry)
        full_mfe = max(full_mfe, favourable)
        full_mae = max(full_mae, adverse)
        maximum_close_profit = max(maximum_close_profit, close_profit)
        if exit_i is not None:
            continue
        exit_mfe = max(exit_mfe, favourable)
        exit_mae = max(exit_mae, adverse)
        hit_stop = low <= active_stop if direction > 0 else high >= active_stop
        hit_target = bool(
            spec["kind"] == "fixed"
            and (high >= target if direction > 0 else low <= target)
        )
        if hit_stop:
            exit_i = i
            exit_price = _stop_fill(open_price, active_stop, direction)
            outcome = f"{stop_source}_stop"
            continue
        if hit_target:
            exit_i = i
            exit_price = target
            outcome = "fixed_target"
            continue
        if spec["kind"] == "runner":
            if (
                not runner_armed
                and close_profit >= float(execution["runner_activation_atr"]) * signal_atr
            ):
                runner_armed = True
                armed_i = i
            if runner_armed:
                runner_value = float(frame.loc[i, str(spec["runner"])])
                current_atr = float(frame.loc[i, "atr"])
                if np.isfinite(runner_value) and np.isfinite(current_atr):
                    candidate = runner_value - direction * float(spec["buffer"]) * current_atr
                    if direction > 0 and candidate > active_stop:
                        active_stop = candidate
                        stop_source = str(spec["runner"])
                    elif direction < 0 and candidate < active_stop:
                        active_stop = candidate
                        stop_source = str(spec["runner"])
    if exit_i is None:
        exit_i = end_i
        exit_price = float(frame.loc[end_i, "close"])
        outcome = "timeout"
    gross = direction * (float(exit_price) / entry - 1.0)
    cost = float(execution["round_trip_cost_fraction"])
    risk_fraction = float(execution["initial_disaster_stop_atr"]) * signal_atr / entry
    realised_distance = direction * (float(exit_price) - entry)
    return {
        "resolved": True,
        "exit_policy": policy,
        "outcome": outcome,
        "exit_i": exit_i,
        "exit_time": frame.loc[exit_i, "open_time"] + pd.Timedelta(minutes=minutes),
        "exit_price": float(exit_price),
        "hard_stop_price": hard_stop,
        "final_active_stop": active_stop,
        "hold_bars": exit_i - entry_i + 1,
        "horizon_bars": horizon,
        "gross_return": gross,
        "net_return": gross - cost,
        "risk_fraction": risk_fraction,
        "return_r": gross / risk_fraction if risk_fraction > 0 else np.nan,
        "net_return_r": (gross - cost) / risk_fraction if risk_fraction > 0 else np.nan,
        "runner_armed": runner_armed,
        "armed_i": armed_i,
        "armed_time": frame.loc[armed_i, "open_time"] + pd.Timedelta(minutes=minutes)
        if armed_i is not None
        else pd.NaT,
        "mfe_at_exit_atr": exit_mfe / signal_atr,
        "mae_at_exit_atr": exit_mae / signal_atr,
        "horizon_mfe_atr": full_mfe / signal_atr,
        "horizon_mae_atr": full_mae / signal_atr,
        "horizon_max_close_profit_atr": maximum_close_profit / signal_atr,
        "exit_giveback_atr": (exit_mfe - realised_distance) / signal_atr,
    }


def resolve_period(
    accepted: pd.DataFrame,
    frame: pd.DataFrame,
    config: Mapping[str, Any],
    *,
    policy: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    if accepted.empty:
        return accepted.copy()
    spec = parse_exit_policy(policy)
    rows: list[dict[str, Any]] = []
    for event in accepted.to_dict("records"):
        entry_time = utc(event["entry_time"])
        minutes = int(event["bar_minutes"])
        horizon = int(spec["horizon_hours"] * 60 // minutes)
        last_i = int(event["entry_i"]) + horizon - 1
        if not (start <= entry_time < end) or last_i >= len(frame):
            continue
        if frame.loc[last_i, "open_time"] + pd.Timedelta(minutes=minutes) > end:
            continue
        result = resolve_trade(frame, event, config, policy=policy)
        if result.get("resolved"):
            rows.append({**event, **result})
    return pd.DataFrame(rows)


def _equity_metrics(values: Iterable[float]) -> tuple[float, float]:
    array = np.asarray(list(values), dtype=float)
    array = array[np.isfinite(array)]
    if not len(array):
        return np.nan, np.nan
    equity = np.cumprod(1.0 + 0.01 * array)
    peaks = np.maximum.accumulate(np.concatenate(([1.0], equity)))[:-1]
    drawdowns = equity / peaks - 1.0
    return float(equity[-1] - 1.0), float(drawdowns.min())


def metrics(events: pd.DataFrame) -> dict[str, Any]:
    if events.empty:
        return {
            "events": 0,
            "mean_gross_bp": np.nan,
            "mean_net_bp": np.nan,
            "median_net_bp": np.nan,
            "win_rate": np.nan,
            "profit_factor": np.nan,
            "equal_risk_1pct_return": np.nan,
            "max_drawdown": np.nan,
            "median_hold_bars": np.nan,
            "p95_net_bp": np.nan,
            "p99_net_bp": np.nan,
            "max_net_bp": np.nan,
            "runner_arm_rate": np.nan,
        }
    equal_return, drawdown = _equity_metrics(events["net_return_r"])
    return {
        "events": int(len(events)),
        "mean_gross_bp": float(events["gross_return"].mean() * 1e4),
        "mean_net_bp": float(events["net_return"].mean() * 1e4),
        "median_net_bp": float(events["net_return"].median() * 1e4),
        "win_rate": float(events["net_return"].gt(0.0).mean()),
        "profit_factor": float(profit_factor(events["net_return"])),
        "equal_risk_1pct_return": equal_return,
        "max_drawdown": drawdown,
        "median_hold_bars": float(events["hold_bars"].median()),
        "p95_net_bp": float(events["net_return"].quantile(0.95) * 1e4),
        "p99_net_bp": float(events["net_return"].quantile(0.99) * 1e4),
        "max_net_bp": float(events["net_return"].max() * 1e4),
        "runner_arm_rate": float(events["runner_armed"].mean()),
    }


def fold_label(stamp: object) -> str:
    value = utc(stamp)
    if value.year == 2026:
        return "2026P1"
    return f"{value.year}H{1 if value.month <= 6 else 2}"


def fold_table(events: pd.DataFrame, folds: Sequence[str]) -> pd.DataFrame:
    labels = events["entry_time"].map(fold_label) if len(events) else pd.Series(dtype=str)
    return pd.DataFrame(
        [
            {"fold": fold, **metrics(events.loc[labels.eq(fold)].copy())}
            for fold in folds
        ]
    )


def robust_metrics(
    events: pd.DataFrame,
    folds: Sequence[str],
    *,
    minimum_total: int,
    minimum_per_fold: int,
) -> dict[str, Any]:
    table = fold_table(events, folds)
    means = table["mean_net_bp"].to_numpy(dtype=float)
    counts = table["events"].to_numpy(dtype=int)
    finite = bool(len(means) and np.isfinite(means).all())
    result = metrics(events)
    result.update(
        {
            "minimum_fold_events": int(counts.min()) if len(counts) else 0,
            "eligible": bool(
                len(events) >= minimum_total
                and len(counts)
                and np.all(counts >= minimum_per_fold)
                and finite
            ),
            "robust_score_bp": float(np.median(means) - 0.5 * np.std(means, ddof=0))
            if finite
            else np.nan,
            "worst_fold_net_bp": float(np.min(means)) if finite else np.nan,
        }
    )
    for row in table.itertuples(index=False):
        result[f"{row.fold}_events"] = int(row.events)
        result[f"{row.fold}_mean_net_bp"] = float(row.mean_net_bp)
    return result


def prepare_frame(frame: pd.DataFrame, reference: str) -> pd.DataFrame:
    return add_runner_references(add_reference_features(frame, reference))


def evaluate_arm(
    base: pd.DataFrame,
    config: Mapping[str, Any],
    timeframe_spec: Mapping[str, Any],
    params: Mapping[str, str],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    timeframe = str(timeframe_spec["id"])
    minutes = int(timeframe_spec["minutes"])
    frame = prepare_frame(base, str(params["ma_reference"]))
    raw = build_candidates(frame, config["signal"], str(params["entry_family"]))
    accepted = accept_candidates(
        raw,
        frame,
        timeframe=timeframe,
        minutes=minutes,
        cooldown_bars=int(config["signal"]["cooldown_bars"]),
    )
    events = resolve_period(
        accepted,
        frame,
        config,
        policy=str(params["exit_policy"]),
        start=start,
        end=end,
    )
    return frame, accepted, events


def _choose_factor(
    rows: list[dict[str, Any]], incumbent: Mapping[str, Any], config: Mapping[str, Any]
) -> tuple[dict[str, Any] | None, str]:
    selection = config["coordinate_pass"]
    eligible = [row for row in rows if bool(row["eligible"])]
    if not eligible:
        return None, "retain_no_sample_eligible_arm"
    incumbent_score = float(incumbent.get("robust_score_bp", np.nan))
    incumbent_worst = float(incumbent.get("worst_fold_net_bp", np.nan))
    if not np.isfinite(incumbent_score) or not np.isfinite(incumbent_worst):
        eligible.sort(
            key=lambda row: (-float(row["robust_score_bp"]), -float(row["worst_fold_net_bp"]), int(row["grid_index"]))
        )
        return eligible[0], "move_from_ineligible_incumbent"
    passing = [
        row
        for row in eligible
        if float(row["robust_score_bp"])
        >= incumbent_score + float(selection["minimum_robust_improvement_bp"])
        and float(row["worst_fold_net_bp"])
        >= incumbent_worst - float(selection["maximum_worst_fold_degradation_bp"])
    ]
    if not passing:
        return None, "retain_no_preregistered_improvement"
    passing.sort(
        key=lambda row: (
            -float(row["robust_score_bp"]),
            -float(row["worst_fold_net_bp"]),
            -int(row["events"]),
            int(row["grid_index"]),
        )
    )
    return passing[0], "move_by_preregistered_rule"


def discovery_phase(config: dict[str, Any]) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    eth_frames, _, quality = load_frames(config)
    start = utc(config["time_contract"]["discovery_start_inclusive"])
    end = utc(config["time_contract"]["discovery_end_exclusive"])
    folds = list(config["time_contract"]["discovery_folds"])
    traces: list[dict[str, Any]] = []
    selected_trades: list[pd.DataFrame] = []
    selections: dict[str, Any] = {}
    for timeframe_spec in config["timeframes"]:
        timeframe = str(timeframe_spec["id"])
        params = deepcopy(config["coordinate_pass"]["initial"])
        cache: dict[str, tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]] = {}

        def evaluate(current: Mapping[str, str]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
            key = json.dumps(dict(current), sort_keys=True)
            if key not in cache:
                frame, accepted, events = evaluate_arm(
                    eth_frames[timeframe], config, timeframe_spec, current, start, end
                )
                result = robust_metrics(
                    events,
                    folds,
                    minimum_total=int(timeframe_spec["minimum_total"]),
                    minimum_per_fold=int(timeframe_spec["minimum_per_fold"]),
                )
                cache[key] = frame, accepted, events, result
            return cache[key]

        _, _, initial_events, initial_metrics = evaluate(params)
        steps: list[dict[str, Any]] = []
        for step, factor_spec in enumerate(config["coordinate_pass"]["ordered_factors"], 1):
            factor = str(factor_spec["factor"])
            _, _, _, incumbent_metrics = evaluate(params)
            current_rows: list[dict[str, Any]] = []
            for grid_index, value in enumerate(factor_spec["values"]):
                arm = deepcopy(params)
                arm[factor] = str(value)
                _, _, _, result = evaluate(arm)
                row = {
                    "timeframe": timeframe,
                    "step": step,
                    "factor": factor,
                    "value": str(value),
                    "grid_index": grid_index,
                    **result,
                }
                current_rows.append(row)
                traces.append(row)
            chosen, reason = _choose_factor(current_rows, incumbent_metrics, config)
            before = deepcopy(params)
            if chosen is not None:
                params[factor] = str(chosen["value"])
            _, _, _, selected_metrics = evaluate(params)
            steps.append(
                {
                    "step": step,
                    "factor": factor,
                    "before": before,
                    "after": deepcopy(params),
                    "reason": reason,
                    "selected_metrics": selected_metrics,
                }
            )
            print(
                f"{timeframe} {factor}: {before[factor]} -> {params[factor]} "
                f"{reason}; robust={selected_metrics['robust_score_bp']:.2f}bp n={selected_metrics['events']}",
                flush=True,
            )
        frame, accepted, events, selected_metrics = evaluate(params)
        if len(events):
            current = events.copy()
            current["timeframe"] = timeframe
            selected_trades.append(current)
        selections[timeframe] = {
            "minutes": int(timeframe_spec["minutes"]),
            "initial_params": config["coordinate_pass"]["initial"],
            "initial_metrics": initial_metrics,
            "selected_params": params,
            "selected_metrics": selected_metrics,
            "steps": steps,
            "accepted_candidates": len(accepted),
            "featured_rows": len(frame),
        }
    write_csv(pd.DataFrame(traces), RESULTS / "discovery_coordinate_trace.csv")
    all_trades = pd.concat(selected_trades, ignore_index=True, sort=False) if selected_trades else pd.DataFrame()
    write_csv(all_trades, RESULTS / "discovery_selected_trades.csv.gz")
    receipt = {
        "phase": "discovery_complete_confirmation_unopened",
        "config_sha256": sha256_file(CONFIG_PATH),
        "script_sha256": sha256_file(SCRIPT_PATH),
        "source": quality,
        "timeframes": selections,
        "holdout_rows_read": 0,
    }
    write_json(SELECTION_PATH, receipt)
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))


def assert_committed(paths: Sequence[Path], *, phase: str) -> None:
    relative = [str(path.relative_to(ROOT)) for path in paths]
    subprocess.run(
        ["git", "ls-files", "--error-unmatch", *relative],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", *relative],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if dirty:
        raise RuntimeError(f"{phase} inputs must be committed: {dirty}")


def assert_discovery_committed() -> dict[str, Any]:
    receipt = json.loads(SELECTION_PATH.read_text(encoding="utf-8"))
    assert_committed([CONFIG_PATH, SCRIPT_PATH, SELECTION_PATH], phase="confirmation")
    if receipt.get("phase") != "discovery_complete_confirmation_unopened":
        raise RuntimeError("discovery receipt phase drift")
    if receipt.get("config_sha256") != sha256_file(CONFIG_PATH):
        raise RuntimeError("config changed after discovery")
    if receipt.get("script_sha256") != sha256_file(SCRIPT_PATH):
        raise RuntimeError("script changed after discovery")
    return receipt


def _rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0.0)
    down = -delta.clip(upper=0.0)
    gain = pd.Series(pine_rma(up.to_numpy(dtype=float), length), index=close.index)
    loss = pd.Series(pine_rma(down.to_numpy(dtype=float), length), index=close.index)
    rs = gain / loss.replace(0.0, np.nan)
    return 100.0 - 100.0 / (1.0 + rs)


def _adx(frame: pd.DataFrame, length: int = 14) -> pd.Series:
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    close = frame["close"].astype(float)
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    previous = close.shift(1)
    tr = np.maximum(high - low, np.maximum((high - previous).abs(), (low - previous).abs()))
    atr = pd.Series(pine_rma(np.asarray(tr, dtype=float), length), index=frame.index)
    plus = 100.0 * pd.Series(pine_rma(np.asarray(plus_dm, dtype=float), length), index=frame.index) / atr
    minus = 100.0 * pd.Series(pine_rma(np.asarray(minus_dm, dtype=float), length), index=frame.index) / atr
    dx = 100.0 * (plus - minus).abs() / (plus + minus).replace(0.0, np.nan)
    return pd.Series(pine_rma(dx.to_numpy(dtype=float), length), index=frame.index)


def _run_length(values: pd.Series) -> pd.Series:
    array = values.fillna(False).to_numpy(dtype=bool)
    result = np.zeros(len(array), dtype=float)
    current = 0
    for index, value in enumerate(array):
        current = current + 1 if value else 0
        result[index] = current
    return pd.Series(result, index=values.index)


def market_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Build causal bar features from current/prior OHLCV within each segment."""

    parts: list[pd.DataFrame] = []
    for _, segment in frame.groupby("segment_id", sort=True):
        out = segment.copy()
        close = out["close"].astype(float)
        high = out["high"].astype(float)
        low = out["low"].astype(float)
        volume = out["volume"].astype(float)
        atr = out["atr"].astype(float).replace(0.0, np.nan)
        hl2 = (high + low) / 2.0
        returns = close.pct_change()
        out["rsi14"] = _rsi(close)
        out["adx14"] = _adx(out)
        out["atr_pct"] = atr / close
        out["atr_rel96"] = atr / atr.rolling(96, min_periods=48).median()
        out["range_atr_bar"] = (high - low) / atr
        for window in (8, 24, 96):
            out[f"rv_{window}"] = returns.rolling(window, min_periods=max(4, window // 2)).std()
            out[f"volume_ratio_{window}"] = volume / volume.rolling(window, min_periods=max(4, window // 2)).mean()
        volume_mean = volume.rolling(96, min_periods=48).mean()
        volume_std = volume.rolling(96, min_periods=48).std()
        out["volume_z96"] = (volume - volume_mean) / volume_std.replace(0.0, np.nan)
        for label in ("SMA20", "EMA20", "SMA40", "EMA40", "SMA60", "EMA60", "SMA120", "EMA120"):
            ma = _reference(out, label)
            key = label.lower()
            out[f"close_{key}_atr"] = (close - ma) / atr
            out[f"slope_{key}_atr"] = (ma - ma.shift(4)) / (atr * 4.0)
        out["ma_spread_20_60_atr"] = (_reference(out, "EMA20") - _reference(out, "SMA60")) / atr
        out["ma_spread_60_120_atr"] = (_reference(out, "SMA60") - _reference(out, "SMA120")) / atr
        out["correct_side_run"] = _run_length(close >= _reference(out, "SMA60"))
        for lag in (1, 2, 4, 8, 16, 32):
            out[f"ret_{lag}"] = close / close.shift(lag) - 1.0
        rolling_high20 = high.rolling(20, min_periods=20).max()
        rolling_low20 = low.rolling(20, min_periods=20).min()
        rolling_high60 = high.rolling(60, min_periods=30).max()
        rolling_low60 = low.rolling(60, min_periods=30).min()
        out["location20"] = (close - rolling_low20) / (rolling_high20 - rolling_low20).replace(0.0, np.nan)
        out["location60"] = (close - rolling_low60) / (rolling_high60 - rolling_low60).replace(0.0, np.nan)
        bar_range = (high - low).replace(0.0, np.nan)
        body_low = np.minimum(out["open"], close)
        body_high = np.maximum(out["open"], close)
        out["upper_wick_share"] = (high - body_high) / bar_range
        out["lower_wick_share"] = (body_low - low) / bar_range
        out["seq_mean16"] = returns.rolling(16, min_periods=12).mean()
        out["seq_std16"] = returns.rolling(16, min_periods=12).std()
        out["seq_skew16"] = returns.rolling(16, min_periods=12).skew()
        out["seq_autocorr16"] = returns.rolling(16, min_periods=12).apply(
            lambda values: pd.Series(values).autocorr(lag=1), raw=False
        )
        out["seq_up_share16"] = returns.gt(0.0).rolling(16, min_periods=12).mean()
        out["seq_compression16"] = (high.rolling(16).max() - low.rolling(16).min()) / atr
        out["hl2"] = hl2
        parts.append(out)
    return pd.concat(parts, ignore_index=True) if parts else frame.copy()


def _parent_minutes(minutes: int) -> int:
    return {5: 15, 15: 60, 30: 120, 60: 240, 120: 480, 240: 1440}[minutes]


GEOMETRY_FEATURES = [
    "direction",
    "signal_score",
    "signed_close_atr",
    "signed_slope_atr_per_bar",
    "signed_body_atr",
    "breakout_atr",
    "prior_near_ma_share",
    "prior_range_atr",
    "flag_k1k2",
    "flag_direct",
    "flag_rejection",
    "flag_coil",
    "gap_bars",
    "k1_body_ratio",
    "k1_range_atr",
    "k1_close_location",
    "k2_wick_share",
    "k2_body_ratio",
    "k2_rejection_close_location",
    "k2_touch_depth_atr",
    "path_correct_side_share",
]
TREND_FEATURES = [
    *[f"signed_close_{label}_atr" for label in ("sma20", "ema20", "sma40", "ema40", "sma60", "ema60", "sma120", "ema120")],
    *[f"signed_slope_{label}_atr" for label in ("sma20", "ema20", "sma40", "ema40", "sma60", "ema60", "sma120", "ema120")],
    "signed_ma_spread_20_60_atr",
    "signed_ma_spread_60_120_atr",
    "directional_correct_side_run",
]
VOLATILITY_FEATURES = ["atr_pct", "atr_rel96", "range_atr_bar", "rv_8", "rv_24", "rv_96"]
PARTICIPATION_FEATURES = ["volume_ratio_8", "volume_ratio_24", "volume_ratio_96", "volume_z96"]
STRUCTURE_FEATURES = [
    "directional_rsi14",
    "adx14",
    *[f"directional_ret_{lag}" for lag in (1, 2, 4, 8, 16, 32)],
    "directional_location20",
    "directional_location60",
    "directional_wick_rejection",
]
SEQUENCE_FEATURES = [
    "directional_seq_mean16",
    "seq_std16",
    "directional_seq_skew16",
    "seq_autocorr16",
    "directional_seq_up_share16",
    "seq_compression16",
]
HIGHER_FEATURES = [
    "parent_directional_close_sma60_atr",
    "parent_directional_slope_sma60_atr",
    "parent_directional_rsi14",
    "parent_adx14",
    "parent_directional_ret4",
    "parent_atr_rel96",
]
BTC_FEATURES = [
    "btc_directional_ret1",
    "btc_directional_ret4",
    "btc_directional_ret16",
    "btc_directional_rsi14",
    "btc_directional_slope_sma60_atr",
    "eth_btc_directional_relative_ret16",
    "eth_btc_corr96",
]
FEATURE_GROUPS = {
    "geometry": GEOMETRY_FEATURES,
    "trend": TREND_FEATURES,
    "volatility": VOLATILITY_FEATURES,
    "participation": PARTICIPATION_FEATURES,
    "structure": STRUCTURE_FEATURES,
    "sequence": SEQUENCE_FEATURES,
    "higher_timeframe": HIGHER_FEATURES,
    "btc_resonance": BTC_FEATURES,
}
ALL_FEATURES = list(dict.fromkeys(sum(FEATURE_GROUPS.values(), [])))


def attach_features(
    events: pd.DataFrame,
    eth_frame: pd.DataFrame,
    btc_frame: pd.DataFrame,
    native_eth: pd.DataFrame,
    *,
    minutes: int,
    atr_length: int,
) -> pd.DataFrame:
    """Attach causal event, same-bar, BTC and last-complete parent-bar features."""

    if events.empty:
        return events.copy()
    market = market_features(eth_frame)
    btc_market = market_features(btc_frame)
    output = events.copy().reset_index(drop=True)
    signal_indices = output["signal_i"].astype(int).to_numpy()
    selected = market.iloc[signal_indices].reset_index(drop=True)
    direction = output["direction"].to_numpy(dtype=float)
    families = output["signal_family"].fillna("").astype(str)
    output["flag_k1k2"] = families.str.contains("k1k2").astype(float)
    output["flag_direct"] = families.str.contains("direct").astype(float)
    output["flag_rejection"] = families.str.contains("rejection").astype(float)
    output["flag_coil"] = families.str.contains("coil").astype(float)
    for column in GEOMETRY_FEATURES:
        if column not in output:
            output[column] = np.nan
    for label in ("sma20", "ema20", "sma40", "ema40", "sma60", "ema60", "sma120", "ema120"):
        output[f"signed_close_{label}_atr"] = direction * selected[f"close_{label}_atr"].to_numpy(dtype=float)
        output[f"signed_slope_{label}_atr"] = direction * selected[f"slope_{label}_atr"].to_numpy(dtype=float)
    output["signed_ma_spread_20_60_atr"] = direction * selected["ma_spread_20_60_atr"].to_numpy(dtype=float)
    output["signed_ma_spread_60_120_atr"] = direction * selected["ma_spread_60_120_atr"].to_numpy(dtype=float)
    run = selected["correct_side_run"].to_numpy(dtype=float)
    output["directional_correct_side_run"] = np.where(direction > 0, run, np.nan)
    for column in VOLATILITY_FEATURES + PARTICIPATION_FEATURES:
        output[column] = selected[column].to_numpy(dtype=float)
    output["directional_rsi14"] = direction * (selected["rsi14"].to_numpy(dtype=float) - 50.0) / 50.0
    output["adx14"] = selected["adx14"].to_numpy(dtype=float)
    for lag in (1, 2, 4, 8, 16, 32):
        output[f"directional_ret_{lag}"] = direction * selected[f"ret_{lag}"].to_numpy(dtype=float)
    output["directional_location20"] = np.where(
        direction > 0,
        selected["location20"].to_numpy(dtype=float),
        1.0 - selected["location20"].to_numpy(dtype=float),
    )
    output["directional_location60"] = np.where(
        direction > 0,
        selected["location60"].to_numpy(dtype=float),
        1.0 - selected["location60"].to_numpy(dtype=float),
    )
    output["directional_wick_rejection"] = np.where(
        direction > 0,
        selected["lower_wick_share"].to_numpy(dtype=float),
        selected["upper_wick_share"].to_numpy(dtype=float),
    )
    output["directional_seq_mean16"] = direction * selected["seq_mean16"].to_numpy(dtype=float)
    output["seq_std16"] = selected["seq_std16"].to_numpy(dtype=float)
    output["directional_seq_skew16"] = direction * selected["seq_skew16"].to_numpy(dtype=float)
    output["seq_autocorr16"] = selected["seq_autocorr16"].to_numpy(dtype=float)
    output["directional_seq_up_share16"] = np.where(
        direction > 0,
        selected["seq_up_share16"].to_numpy(dtype=float),
        1.0 - selected["seq_up_share16"].to_numpy(dtype=float),
    )
    output["seq_compression16"] = selected["seq_compression16"].to_numpy(dtype=float)

    btc_subset = btc_market[
        ["open_time", "ret_1", "ret_4", "ret_16", "rsi14", "slope_sma60_atr"]
    ].rename(columns={column: f"btc_{column}" for column in ("ret_1", "ret_4", "ret_16", "rsi14", "slope_sma60_atr")})
    aligned = market[["open_time", "ret_16"]].merge(btc_subset, on="open_time", how="left")
    aligned_selected = aligned.iloc[signal_indices].reset_index(drop=True)
    output["btc_directional_ret1"] = direction * aligned_selected["btc_ret_1"].to_numpy(dtype=float)
    output["btc_directional_ret4"] = direction * aligned_selected["btc_ret_4"].to_numpy(dtype=float)
    output["btc_directional_ret16"] = direction * aligned_selected["btc_ret_16"].to_numpy(dtype=float)
    output["btc_directional_rsi14"] = direction * (aligned_selected["btc_rsi14"].to_numpy(dtype=float) - 50.0) / 50.0
    output["btc_directional_slope_sma60_atr"] = direction * aligned_selected["btc_slope_sma60_atr"].to_numpy(dtype=float)
    output["eth_btc_directional_relative_ret16"] = direction * (
        aligned_selected["ret_16"].to_numpy(dtype=float) - aligned_selected["btc_ret_16"].to_numpy(dtype=float)
    )
    merged_returns = market[["open_time", "ret_1"]].merge(
        btc_market[["open_time", "ret_1"]].rename(columns={"ret_1": "btc_ret_1"}),
        on="open_time",
        how="left",
    )
    merged_returns["corr96"] = merged_returns["ret_1"].rolling(96, min_periods=48).corr(merged_returns["btc_ret_1"])
    output["eth_btc_corr96"] = merged_returns.iloc[signal_indices]["corr96"].to_numpy(dtype=float)

    parent_minutes = _parent_minutes(minutes)
    parent = market_features(
        aggregate_timeframe(native_eth, minutes=parent_minutes, atr_length=atr_length)
    )
    parent_context = pd.DataFrame(
        {
            "available_at": parent["open_time"] + pd.Timedelta(minutes=parent_minutes),
            "parent_close_sma60_atr": parent["close_sma60_atr"],
            "parent_slope_sma60_atr": parent["slope_sma60_atr"],
            "parent_rsi14": parent["rsi14"],
            "parent_adx14": parent["adx14"],
            "parent_ret4": parent["ret_4"],
            "parent_atr_rel96": parent["atr_rel96"],
        }
    ).sort_values("available_at")
    decisions = pd.DataFrame(
        {
            "row": np.arange(len(output)),
            "decision_time": pd.to_datetime(output["signal_time"], utc=True) + pd.Timedelta(minutes=minutes),
        }
    ).sort_values("decision_time")
    parent_aligned = pd.merge_asof(
        decisions,
        parent_context,
        left_on="decision_time",
        right_on="available_at",
        direction="backward",
        allow_exact_matches=True,
    ).sort_values("row")
    output["parent_directional_close_sma60_atr"] = direction * parent_aligned["parent_close_sma60_atr"].to_numpy(dtype=float)
    output["parent_directional_slope_sma60_atr"] = direction * parent_aligned["parent_slope_sma60_atr"].to_numpy(dtype=float)
    output["parent_directional_rsi14"] = direction * (parent_aligned["parent_rsi14"].to_numpy(dtype=float) - 50.0) / 50.0
    output["parent_adx14"] = parent_aligned["parent_adx14"].to_numpy(dtype=float)
    output["parent_directional_ret4"] = direction * parent_aligned["parent_ret4"].to_numpy(dtype=float)
    output["parent_atr_rel96"] = parent_aligned["parent_atr_rel96"].to_numpy(dtype=float)
    return output.replace([np.inf, -np.inf], np.nan)


def feature_list(variant: str) -> list[str]:
    if variant == "rule_score":
        return []
    if variant == "geometry":
        groups = ["geometry"]
    elif variant.startswith("geometry_"):
        groups = ["geometry", variant.removeprefix("geometry_")]
    else:
        groups = list(FEATURE_GROUPS)
    return list(dict.fromkeys(sum((FEATURE_GROUPS[group] for group in groups), [])))


def _matrix(events: pd.DataFrame, columns: Sequence[str]) -> np.ndarray:
    return events.reindex(columns=columns).astype(float).replace([np.inf, -np.inf], np.nan).to_numpy()


def _model_params(config: Mapping[str, Any]) -> dict[str, Any]:
    return dict(config["feature_screen"]["model"])


def fit_score_contract(
    train: pd.DataFrame,
    variant: str,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    from lightgbm import LGBMClassifier, LGBMRegressor

    top_fraction = float(config["feature_screen"]["top_fraction"])
    if variant == "rule_score":
        scores = train["signal_score"].to_numpy(dtype=float)
        return {
            "variant": variant,
            "features": [],
            "models": {},
            "threshold": float(np.nanquantile(scores, 1.0 - top_fraction)),
            "normalization": {},
        }
    features = feature_list(variant)
    params = _model_params(config)
    x = _matrix(train, features)
    return_model = LGBMRegressor(objective="huber", **params)
    return_model.fit(x, train["net_return"].to_numpy(dtype=float) * 1e4)
    return_scores = return_model.predict(x)
    models: dict[str, Any] = {"return": return_model}
    normalization: dict[str, float] = {}
    if variant == "all_arm_probability" or variant == "all_two_stage":
        labels = train["runner_armed"].astype(int).to_numpy()
        if len(np.unique(labels)) < 2:
            arm_model = None
            arm_scores = np.full(len(train), float(labels.mean()))
        else:
            arm_model = LGBMClassifier(objective="binary", **params)
            arm_model.fit(x, labels)
            arm_scores = arm_model.predict_proba(x)[:, 1]
        models["arm"] = arm_model
        if variant == "all_arm_probability":
            scores = arm_scores
        else:
            arm_clipped = np.clip(arm_scores, 1e-6, 1.0 - 1e-6)
            arm_logit = np.log(arm_clipped / (1.0 - arm_clipped))
            return_mean = float(np.mean(return_scores))
            return_std = float(np.std(return_scores)) or 1.0
            arm_mean = float(np.mean(arm_logit))
            arm_std = float(np.std(arm_logit)) or 1.0
            normalization = {
                "return_mean": return_mean,
                "return_std": return_std,
                "arm_logit_mean": arm_mean,
                "arm_logit_std": arm_std,
            }
            scores = (return_scores - return_mean) / return_std + (arm_logit - arm_mean) / arm_std
    else:
        scores = return_scores
    return {
        "variant": variant,
        "features": features,
        "models": models,
        "threshold": float(np.nanquantile(scores, 1.0 - top_fraction)),
        "normalization": normalization,
    }


def predict_score(contract: Mapping[str, Any], events: pd.DataFrame) -> np.ndarray:
    variant = str(contract["variant"])
    if variant == "rule_score":
        return events["signal_score"].to_numpy(dtype=float)
    x = _matrix(events, contract["features"])
    return_scores = contract["models"]["return"].predict(x)
    if variant == "all_arm_probability" or variant == "all_two_stage":
        arm_model = contract["models"].get("arm")
        arm_scores = (
            arm_model.predict_proba(x)[:, 1]
            if arm_model is not None
            else np.full(len(events), 0.5)
        )
        if variant == "all_arm_probability":
            return arm_scores
        norm = contract["normalization"]
        clipped = np.clip(arm_scores, 1e-6, 1.0 - 1e-6)
        logits = np.log(clipped / (1.0 - clipped))
        return (return_scores - norm["return_mean"]) / norm["return_std"] + (
            logits - norm["arm_logit_mean"]
        ) / norm["arm_logit_std"]
    return return_scores


def binary_auc(labels: Iterable[bool], scores: Iterable[float]) -> float:
    y = np.asarray(list(labels), dtype=bool)
    s = np.asarray(list(scores), dtype=float)
    finite = np.isfinite(s)
    y = y[finite]
    s = s[finite]
    positives = int(y.sum())
    negatives = int((~y).sum())
    if not positives or not negatives:
        return np.nan
    ranks = pd.Series(s).rank(method="average").to_numpy(dtype=float)
    return float((ranks[y].sum() - positives * (positives + 1) / 2.0) / (positives * negatives))


def block_signflip_p(
    values: pd.Series,
    times: pd.Series,
    *,
    seed: int,
    resamples: int = 20000,
) -> float:
    frame = pd.DataFrame(
        {
            "value": pd.to_numeric(values, errors="coerce"),
            "time": pd.to_datetime(times, utc=True, errors="coerce"),
        }
    ).dropna()
    if frame.empty:
        return np.nan
    iso = frame["time"].dt.isocalendar()
    frame["block"] = iso["year"].astype(str) + "-" + iso["week"].astype(str)
    blocks = frame.groupby("block", sort=True)["value"].mean().to_numpy(dtype=float)
    observed = float(np.mean(blocks))
    if observed <= 0.0:
        return 1.0
    if len(blocks) <= 18:
        patterns = np.arange(1 << len(blocks), dtype=np.uint64)[:, None]
        bits = (patterns >> np.arange(len(blocks), dtype=np.uint64)) & 1
        signs = bits.astype(float) * 2.0 - 1.0
        simulated = (signs * blocks).mean(axis=1)
    else:
        rng = np.random.default_rng(seed)
        signs = rng.choice((-1.0, 1.0), size=(resamples, len(blocks)))
        simulated = (signs * blocks).mean(axis=1)
    return float((1.0 + np.sum(simulated >= observed)) / (len(simulated) + 1.0))


def evaluate_scores(
    events: pd.DataFrame,
    scores: np.ndarray,
    threshold: float,
    folds: Sequence[str],
    *,
    p_seed: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    scored = events.copy()
    scored["model_score"] = scores
    selected = scored.loc[np.isfinite(scores) & (scores >= threshold)].copy()
    table = fold_table(selected, folds)
    means = table["mean_net_bp"].to_numpy(dtype=float)
    result = {
        **metrics(selected),
        "score_threshold": threshold,
        "positive_net_auc": binary_auc(scored["net_return"].gt(0.0), scores),
        "runner_arm_auc": binary_auc(scored["runner_armed"], scores),
        "raw_score_p": block_signflip_p(
            selected["net_return"], selected["entry_time"], seed=p_seed
        )
        if len(selected)
        else np.nan,
        "minimum_fold_events": int(table["events"].min()) if len(table) else 0,
        "worst_fold_net_bp": float(np.nanmin(means)) if np.isfinite(means).any() else np.nan,
        "robust_score_bp": float(np.nanmedian(means) - 0.5 * np.nanstd(means))
        if np.isfinite(means).any()
        else np.nan,
    }
    for row in table.itertuples(index=False):
        result[f"{row.fold}_events"] = int(row.events)
        result[f"{row.fold}_mean_net_bp"] = float(row.mean_net_bp)
    return selected, result


def _atr_buckets(frame: pd.DataFrame, eligible: np.ndarray) -> np.ndarray:
    buckets = np.full(len(frame), -1, dtype=int)
    helper = pd.DataFrame(
        {
            "i": np.arange(len(frame)),
            "month": frame["open_time"].dt.strftime("%Y-%m"),
            "atr": frame["atr"],
            "eligible": eligible,
        }
    )
    for _, group in helper[helper["eligible"] & helper["atr"].notna()].groupby("month", sort=True):
        labels = pd.qcut(group["atr"].rank(method="first"), q=min(5, len(group)), labels=False).fillna(0)
        buckets[group["i"].to_numpy(dtype=int)] = labels.to_numpy(dtype=int)
    return buckets


def matched_controls(
    events: pd.DataFrame,
    all_candidates: pd.DataFrame,
    frame: pd.DataFrame,
    config: Mapping[str, Any],
    *,
    policy: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    timeframe: str,
    minutes: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if events.empty:
        return pd.DataFrame(), pd.DataFrame()
    horizon = int(parse_exit_policy(policy)["horizon_hours"] * 60 // minutes)
    eligible = np.zeros(len(frame), dtype=bool)
    for signal_i in range(len(frame) - horizon - 1):
        entry_i = signal_i + 1
        last_i = entry_i + horizon - 1
        eligible[signal_i] = bool(
            start <= frame.loc[entry_i, "open_time"] < end
            and frame.loc[last_i, "open_time"] + pd.Timedelta(minutes=minutes) <= end
            and int(frame.loc[signal_i, "segment_id"]) == int(frame.loc[last_i, "segment_id"])
            and np.isfinite(float(frame.loc[signal_i, "atr"]))
            and np.isfinite(float(frame.loc[signal_i, "runner_sma60"]))
        )
    excluded = np.zeros(len(frame), dtype=bool)
    radius = horizon
    for signal_i in all_candidates["signal_i"].astype(int):
        excluded[max(0, signal_i - radius) : min(len(frame), signal_i + radius + 1)] = True
    buckets = _atr_buckets(frame, eligible)
    months = frame["open_time"].dt.strftime("%Y-%m").to_numpy()
    blocks = (frame["open_time"].dt.hour.to_numpy(dtype=int) // 6).astype(int)
    pool: dict[tuple[str, int, int], list[int]] = {}
    for index in np.flatnonzero(eligible & ~excluded & (buckets >= 0)):
        pool.setdefault((str(months[index]), int(blocks[index]), int(buckets[index])), []).append(int(index))
    required = int(config["matched_control"]["controls_per_event"])
    seed = str(config["matched_control"]["seed"])
    controls: list[dict[str, Any]] = []
    pairs: list[dict[str, Any]] = []
    for event in events.to_dict("records"):
        signal_i = int(event["signal_i"])
        key = (str(months[signal_i]), int(blocks[signal_i]), int(buckets[signal_i]))
        choices = sorted(
            pool.get(key, []),
            key=lambda index: hashlib.sha256(f"{seed}|{timeframe}|{event['setup_id']}|{index}".encode()).hexdigest(),
        )
        current: list[float] = []
        for assignment, control_i in enumerate(choices[:required]):
            control_event = {
                "entry_i": control_i + 1,
                "entry_price": float(frame.loc[control_i + 1, "open"]),
                "direction": int(event["direction"]),
                "signal_atr": float(frame.loc[control_i, "atr"]),
                "bar_minutes": minutes,
            }
            result = resolve_trade(frame, control_event, config, policy=policy)
            if not result.get("resolved"):
                continue
            current.append(float(result["net_return"]))
            controls.append(
                {
                    "candidate_setup_id": event["setup_id"],
                    "timeframe": timeframe,
                    "assignment": assignment,
                    "control_i": control_i,
                    "control_time": frame.loc[control_i, "open_time"],
                    "direction": int(event["direction"]),
                    **result,
                }
            )
        if len(current) == required:
            mean = float(np.mean(current))
            pairs.append(
                {
                    "setup_id": event["setup_id"],
                    "timeframe": timeframe,
                    "entry_time": event["entry_time"],
                    "candidate_net_return": event["net_return"],
                    "control_mean_net_return": mean,
                    "paired_excess_return": float(event["net_return"]) - mean,
                    "matched_control_count": required,
                    "match_status": "matched_exact",
                }
            )
        else:
            pairs.append(
                {
                    "setup_id": event["setup_id"],
                    "timeframe": timeframe,
                    "entry_time": event["entry_time"],
                    "candidate_net_return": event["net_return"],
                    "control_mean_net_return": np.nan,
                    "paired_excess_return": np.nan,
                    "matched_control_count": len(current),
                    "match_status": "unmatched",
                }
            )
    return pd.DataFrame(controls), pd.DataFrame(pairs)


def _minimum_selected(spec: Mapping[str, Any]) -> tuple[int, int]:
    return max(12, int(spec["minimum_total"]) // 10), max(3, int(spec["minimum_per_fold"]) // 4)


def confirmation_phase(config: dict[str, Any]) -> None:
    discovery = assert_discovery_committed()
    eth_frames, btc_frames, quality = load_frames(config)
    native_eth, _ = load_native_5m(locate_eth_source(config), config)
    train_start = utc(config["time_contract"]["discovery_start_inclusive"])
    train_end = utc(config["time_contract"]["discovery_end_exclusive"])
    confirm_start = utc(config["time_contract"]["confirmation_start_inclusive"])
    confirm_end = utc(config["time_contract"]["confirmation_end_exclusive"])
    folds = list(config["time_contract"]["confirmation_folds"])
    variants = list(config["feature_screen"]["variants"])
    comparisons = len(variants) * len(config["timeframes"])
    matrix_rows: list[dict[str, Any]] = []
    selected_parts: list[pd.DataFrame] = []
    control_parts: list[pd.DataFrame] = []
    pair_parts: list[pd.DataFrame] = []
    nominations: dict[str, Any] = {}
    for tf_index, spec in enumerate(config["timeframes"]):
        timeframe = str(spec["id"])
        minutes = int(spec["minutes"])
        params = discovery["timeframes"][timeframe]["selected_params"]
        train_frame, train_candidates, train_events = evaluate_arm(
            eth_frames[timeframe], config, spec, params, train_start, train_end
        )
        confirm_frame, confirm_candidates, confirm_events = evaluate_arm(
            eth_frames[timeframe], config, spec, params, confirm_start, confirm_end
        )
        train_featured = attach_features(
            train_events,
            train_frame,
            btc_frames[timeframe],
            native_eth,
            minutes=minutes,
            atr_length=int(config["signal"]["atr_length"]),
        )
        confirm_featured = attach_features(
            confirm_events,
            confirm_frame,
            btc_frames[timeframe],
            native_eth,
            minutes=minutes,
            atr_length=int(config["signal"]["atr_length"]),
        )
        candidate_rows: list[dict[str, Any]] = []
        candidate_selected: dict[str, pd.DataFrame] = {}
        minimum_total, minimum_fold = _minimum_selected(spec)
        for variant_index, variant in enumerate(variants):
            contract = fit_score_contract(train_featured, variant, config)
            scores = predict_score(contract, confirm_featured)
            selected, result = evaluate_scores(
                confirm_featured,
                scores,
                float(contract["threshold"]),
                folds,
                p_seed=20260904 + tf_index * 101 + variant_index,
            )
            result["familywise_score_p"] = min(1.0, float(result["raw_score_p"]) * comparisons) if np.isfinite(float(result["raw_score_p"])) else np.nan
            result["eligible"] = bool(
                int(result["events"]) >= minimum_total
                and int(result["minimum_fold_events"]) >= minimum_fold
                and np.isfinite(float(result["worst_fold_net_bp"]))
            )
            row = {
                "timeframe": timeframe,
                "bar_minutes": minutes,
                "variant": variant,
                "feature_count": len(contract["features"]),
                **result,
            }
            matrix_rows.append(row)
            candidate_rows.append(row)
            candidate_selected[variant] = selected
            print(
                f"confirm {timeframe} {variant}: n={result['events']} mean={result['mean_net_bp']:.2f}bp "
                f"worst={result['worst_fold_net_bp']:.2f} pF={result['familywise_score_p']:.4f}",
                flush=True,
            )
        eligible = [row for row in candidate_rows if row["eligible"]]
        if not eligible:
            eligible = candidate_rows
        eligible.sort(
            key=lambda row: (
                -float(row["worst_fold_net_bp"]) if np.isfinite(float(row["worst_fold_net_bp"])) else math.inf,
                -float(row["mean_net_bp"]) if np.isfinite(float(row["mean_net_bp"])) else math.inf,
                float(row["familywise_score_p"]) if np.isfinite(float(row["familywise_score_p"])) else math.inf,
                -int(row["events"]),
                variants.index(str(row["variant"])),
            )
        )
        winner = eligible[0]
        winner_events = candidate_selected[str(winner["variant"])].copy()
        winner_events["nominated_variant"] = str(winner["variant"])
        selected_parts.append(winner_events)
        controls, pairs = matched_controls(
            winner_events,
            confirm_candidates,
            confirm_frame,
            config,
            policy=str(params["exit_policy"]),
            start=confirm_start,
            end=confirm_end,
            timeframe=timeframe,
            minutes=minutes,
        )
        control_parts.append(controls)
        pair_parts.append(pairs)
        matched = pairs[pairs["match_status"].eq("matched_exact")].copy() if len(pairs) else pd.DataFrame()
        excess_bp = float(matched["paired_excess_return"].mean() * 1e4) if len(matched) else np.nan
        paired_p = block_signflip_p(
            matched["paired_excess_return"], matched["entry_time"], seed=20261904 + tf_index
        ) if len(matched) else np.nan
        halves_positive = all(
            int(winner.get(f"{fold}_events", 0)) < minimum_fold
            or float(winner.get(f"{fold}_mean_net_bp", np.nan)) > 0.0
            for fold in folds
        )
        gates = {
            "mean_net_positive": bool(float(winner["mean_net_bp"]) > 0.0),
            "confirmation_halves_positive": bool(halves_positive),
            "familywise_score_p_lt_0_01": bool(float(winner["familywise_score_p"]) < 0.01),
            "matched_excess_positive": bool(np.isfinite(excess_bp) and excess_bp > 0.0),
            "matched_p_lt_0_01": bool(np.isfinite(paired_p) and paired_p < 0.01),
        }
        gates["all_pass"] = all(gates.values())
        nominations[timeframe] = {
            "selected_params": params,
            "nominated_variant": str(winner["variant"]),
            "features": feature_list(str(winner["variant"])),
            "confirmation_metrics": winner,
            "matched_events": len(matched),
            "matched_control_excess_bp": excess_bp,
            "paired_signflip_p": paired_p,
            "gates": gates,
            "train_pool_events": len(train_featured),
            "confirmation_pool_events": len(confirm_featured),
        }
    write_csv(pd.DataFrame(matrix_rows), RESULTS / "confirmation_model_matrix.csv")
    write_csv(pd.concat(selected_parts, ignore_index=True, sort=False), RESULTS / "confirmation_nominated_trades.csv.gz")
    write_csv(pd.concat(control_parts, ignore_index=True, sort=False), RESULTS / "confirmation_controls.csv.gz")
    write_csv(pd.concat(pair_parts, ignore_index=True, sort=False), RESULTS / "confirmation_control_pairs.csv")
    receipt = {
        "phase": "confirmation_complete_audit_unopened",
        "config_sha256": sha256_file(CONFIG_PATH),
        "script_sha256": sha256_file(SCRIPT_PATH),
        "discovery_receipt_sha256": sha256_file(SELECTION_PATH),
        "source": quality,
        "multiple_comparisons": comparisons,
        "feature_count": len(ALL_FEATURES),
        "timeframes": nominations,
        "holdout_rows_read": 0,
    }
    write_json(CONFIRMATION_PATH, receipt)
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))


def assert_confirmation_committed() -> tuple[dict[str, Any], dict[str, Any]]:
    discovery = json.loads(SELECTION_PATH.read_text(encoding="utf-8"))
    receipt = json.loads(CONFIRMATION_PATH.read_text(encoding="utf-8"))
    assert_committed(
        [CONFIG_PATH, SCRIPT_PATH, SELECTION_PATH, CONFIRMATION_PATH], phase="audit"
    )
    if receipt.get("phase") != "confirmation_complete_audit_unopened":
        raise RuntimeError("confirmation receipt phase drift")
    if receipt.get("config_sha256") != sha256_file(CONFIG_PATH):
        raise RuntimeError("config changed after confirmation")
    if receipt.get("script_sha256") != sha256_file(SCRIPT_PATH):
        raise RuntimeError("script changed after confirmation")
    if receipt.get("discovery_receipt_sha256") != sha256_file(SELECTION_PATH):
        raise RuntimeError("discovery receipt changed after confirmation")
    return discovery, receipt


def failure_mechanics(events: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for event in events.to_dict("records"):
        net = float(event["net_return"])
        armed = bool(event["runner_armed"])
        if net > 0.0:
            category = "winner_large_exit_giveback" if float(event["exit_giveback_atr"]) >= 2.0 else "winner_retained"
        elif str(event["outcome"]) == "hard_stop" and float(event["mfe_at_exit_atr"]) < 0.5:
            category = "false_launch_early_stop"
        elif str(event["outcome"]) == "hard_stop" and float(event["horizon_max_close_profit_atr"]) >= 2.0:
            category = "early_stop_later_close_recovered"
        elif armed:
            category = "armed_then_net_loss"
        elif float(event["gross_return"]) > 0.0:
            category = "gross_win_erased_by_cost"
        elif str(event["outcome"]) == "timeout":
            category = "negative_timeout"
        else:
            category = "failed_before_arm_other"
        rows.append({**event, "failure_category": category})
    return pd.DataFrame(rows)


def audit_phase(config: dict[str, Any]) -> None:
    discovery, confirmation = assert_confirmation_committed()
    eth_frames, btc_frames, quality = load_frames(config)
    native_eth, _ = load_native_5m(locate_eth_source(config), config)
    train_start = utc(config["time_contract"]["discovery_start_inclusive"])
    train_end = utc(config["time_contract"]["confirmation_end_exclusive"])
    audit_start = utc(config["time_contract"]["audit_start_inclusive"])
    audit_end = utc(config["time_contract"]["audit_end_exclusive"])
    slices = list(config["time_contract"]["audit_slices"])
    summary_rows: list[dict[str, Any]] = []
    selected_parts: list[pd.DataFrame] = []
    control_parts: list[pd.DataFrame] = []
    pair_parts: list[pd.DataFrame] = []
    failure_parts: list[pd.DataFrame] = []
    for tf_index, spec in enumerate(config["timeframes"]):
        timeframe = str(spec["id"])
        minutes = int(spec["minutes"])
        params = discovery["timeframes"][timeframe]["selected_params"]
        variant = str(confirmation["timeframes"][timeframe]["nominated_variant"])
        train_frame, _, train_events = evaluate_arm(
            eth_frames[timeframe], config, spec, params, train_start, train_end
        )
        audit_frame, audit_candidates, audit_events = evaluate_arm(
            eth_frames[timeframe], config, spec, params, audit_start, audit_end
        )
        train_featured = attach_features(
            train_events,
            train_frame,
            btc_frames[timeframe],
            native_eth,
            minutes=minutes,
            atr_length=int(config["signal"]["atr_length"]),
        )
        audit_featured = attach_features(
            audit_events,
            audit_frame,
            btc_frames[timeframe],
            native_eth,
            minutes=minutes,
            atr_length=int(config["signal"]["atr_length"]),
        )
        contract = fit_score_contract(train_featured, variant, config)
        scores = predict_score(contract, audit_featured)
        selected, result = evaluate_scores(
            audit_featured,
            scores,
            float(contract["threshold"]),
            slices,
            p_seed=20262904 + tf_index,
        )
        selected["nominated_variant"] = variant
        selected_parts.append(selected)
        controls, pairs = matched_controls(
            selected,
            audit_candidates,
            audit_frame,
            config,
            policy=str(params["exit_policy"]),
            start=audit_start,
            end=audit_end,
            timeframe=timeframe,
            minutes=minutes,
        )
        control_parts.append(controls)
        pair_parts.append(pairs)
        mechanics = failure_mechanics(selected)
        failure_parts.append(mechanics)
        matched = pairs[pairs["match_status"].eq("matched_exact")].copy() if len(pairs) else pd.DataFrame()
        excess_bp = float(matched["paired_excess_return"].mean() * 1e4) if len(matched) else np.nan
        paired_p = block_signflip_p(
            matched["paired_excess_return"], matched["entry_time"], seed=20263904 + tf_index
        ) if len(matched) else np.nan
        _, minimum_slice = _minimum_selected(spec)
        slices_positive = all(
            int(result.get(f"{fold}_events", 0)) < minimum_slice
            or float(result.get(f"{fold}_mean_net_bp", np.nan)) > 0.0
            for fold in slices
        )
        gates = {
            "mean_net_positive": bool(float(result["mean_net_bp"]) > 0.0),
            "audit_slices_positive": bool(slices_positive),
            "score_p_lt_0_01": bool(float(result["raw_score_p"]) < 0.01),
            "matched_excess_positive": bool(np.isfinite(excess_bp) and excess_bp > 0.0),
            "matched_p_lt_0_01": bool(np.isfinite(paired_p) and paired_p < 0.01),
        }
        gates["all_pass"] = all(gates.values())
        summary_rows.append(
            {
                "timeframe": timeframe,
                "bar_minutes": minutes,
                "variant": variant,
                "ma_reference": params["ma_reference"],
                "entry_family": params["entry_family"],
                "exit_policy": params["exit_policy"],
                **result,
                "matched_events": len(matched),
                "matched_control_excess_bp": excess_bp,
                "paired_signflip_p": paired_p,
                **{f"gate_{key}": value for key, value in gates.items()},
            }
        )
        print(
            f"audit {timeframe} {variant}: n={result['events']} mean={result['mean_net_bp']:.2f}bp "
            f"PF={result['profit_factor']:.3f} excess={excess_bp:.2f}bp p={paired_p:.4f} pass={gates['all_pass']}",
            flush=True,
        )
    audit_summary = pd.DataFrame(summary_rows)
    all_selected = pd.concat(selected_parts, ignore_index=True, sort=False)
    all_controls = pd.concat(control_parts, ignore_index=True, sort=False)
    all_pairs = pd.concat(pair_parts, ignore_index=True, sort=False)
    all_failures = pd.concat(failure_parts, ignore_index=True, sort=False)
    failure_summary = (
        all_failures.groupby(["timeframe", "failure_category"], sort=True)
        .agg(
            events=("setup_id", "size"),
            mean_net_bp=("net_return", lambda values: float(np.mean(values) * 1e4)),
            sum_net_bp=("net_return", lambda values: float(np.sum(values) * 1e4)),
            mean_mfe_atr=("mfe_at_exit_atr", "mean"),
            mean_exit_giveback_atr=("exit_giveback_atr", "mean"),
        )
        .reset_index()
    )
    write_csv(audit_summary, RESULTS / "audit_timeframe_summary.csv")
    write_csv(all_selected, RESULTS / "audit_nominated_trades.csv.gz")
    write_csv(all_controls, RESULTS / "audit_controls.csv.gz")
    write_csv(all_pairs, RESULTS / "audit_control_pairs.csv")
    write_csv(all_failures, RESULTS / "audit_failure_ledger.csv.gz")
    write_csv(failure_summary, RESULTS / "audit_failure_summary.csv")
    write_json(
        RESULTS / "audit_summary.json",
        {
            "phase": "historical_transport_audit_complete",
            "source": quality,
            "feature_count": len(ALL_FEATURES),
            "timeframes": summary_rows,
            "any_timeframe_passed": bool(audit_summary["gate_all_pass"].any()),
            "holdout_rows_read": 0,
            "production_eligible": false if False else False,
        },
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True, choices=("discovery", "confirmation", "audit"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config()
    if args.phase == "discovery":
        discovery_phase(config)
    elif args.phase == "confirmation":
        confirmation_phase(config)
    else:
        audit_phase(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
