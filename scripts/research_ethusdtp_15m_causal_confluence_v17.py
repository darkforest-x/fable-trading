#!/usr/bin/env python3
"""Select one causal confluence gate for the frozen ETHUSDT.P 15m V16 ledger.

Feature inputs are limited to the completed signal bar ``t`` and earlier:

* ETH 15m OHLCV, ATR14, EMA30/SMA60, volume, volatility, and structure;
* the latest fully completed ETH 1h and 4h bars available at ``t`` close; and
* BTC 15m plus the latest fully completed BTC 1h bar as a market proxy.

The parent V16 ledger supplies next-open entries and future outcomes.  No
feature reads ``t+1`` or later.  Selection reads 2023--2024 only and corrects
the twelve registered masks with a max-statistic permutation.  The already
seen 2025-through-February-2026 audit ledger is physically unopened until a
passing selection receipt is committed.  Repository holdout rows beginning
2026-05-04 are never parsed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from scripts.backtest_two_key_candle_pine_v8_btc_1h import signflip_p
from scripts.research_btcusdtp_15m_ma_state_trend import (
    fold_label,
    json_value,
    metrics,
    utc,
    write_csv,
    write_json,
)
from scripts.research_ethusdtp_15m_bank_only_runner_v4 import _matched_controls
from scripts.research_two_key_candle_ma_retest_1h import pine_rma, sha256_file

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-ethusdtp-15m-causal-confluence-preholdout-20260905-v17"
EXPERIMENT = ROOT / "experiments" / "active" / EXPERIMENT_ID
CONFIG_PATH = EXPERIMENT / "config.json"
PREREG_PATH = EXPERIMENT / "preregistration.json"
RESULTS = EXPERIMENT / "results"
SELECTION_RECEIPT = RESULTS / "selection_receipt.json"
SCRIPT_PATH = Path(__file__).resolve()
PARENT_CONFIG_PATH = (
    ROOT
    / "experiments/active"
    / "exp-ethusdtp-15m-micro-profit-ladder-preholdout-20260905-v16"
    / "config.json"
)
BAR_DELTA = pd.Timedelta(minutes=15)
FEATURE_COLUMNS = [
    "eth_volume_ratio96",
    "eth_volume_balance12_dir",
    "eth_atr_ratio96",
    "eth_bb_width_ratio96",
    "eth_efficiency24_dir",
    "eth_close_range48_dir",
    "eth_1h_spread_dir_atr",
    "eth_1h_slope_dir_atr",
    "eth_1h_return_dir_atr",
    "eth_4h_spread_dir_atr",
    "eth_4h_slope_dir_atr",
    "btc_15m_spread_dir_atr",
    "btc_15m_slope_dir_atr",
    "btc_1h_spread_dir_atr",
    "btc_1h_slope_dir_atr",
    "axis_eth_1h",
    "axis_eth_4h",
    "axis_btc_stack",
    "axis_participation",
    "axis_expansion",
    "axis_structure",
    "axis_count",
]


def load_config() -> dict[str, Any]:
    """Load the committed experiment contract."""

    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _assert_head_frozen(path: Path) -> None:
    relative = path.relative_to(ROOT).as_posix()
    subprocess.run(
        ["git", "ls-files", "--error-unmatch", relative],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    committed = subprocess.check_output(["git", "show", f"HEAD:{relative}"], cwd=ROOT)
    if hashlib.sha256(committed).digest() != hashlib.sha256(path.read_bytes()).digest():
        raise RuntimeError(f"{relative} differs from frozen HEAD")


def _assert_selection_committed() -> dict[str, Any]:
    _assert_head_frozen(SELECTION_RECEIPT)
    receipt = json.loads(SELECTION_RECEIPT.read_text(encoding="utf-8"))
    if receipt.get("status") != "frozen_for_audit" or not receipt.get(
        "all_registered_gates_pass"
    ):
        raise RuntimeError("selection did not pass or is not committed")
    if not receipt.get("selected_gate_id"):
        raise RuntimeError("selection receipt has no frozen gate")
    return receipt


def _true_range(frame: pd.DataFrame) -> np.ndarray:
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    previous = np.r_[np.nan, close[:-1]]
    return np.nanmax(
        np.vstack((high - low, np.abs(high - previous), np.abs(low - previous))),
        axis=0,
    )


def _safe_div(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator.astype(float).div(denominator.astype(float).replace(0.0, np.nan))


def _load_prefix(
    path: Path,
    *,
    end_exclusive: pd.Timestamp,
    holdout_start: pd.Timestamp,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Read a bounded prefix and refuse chunks that reach repository holdout.

    Inputs are CSV ``open_time/open/high/low/close/volume``.  The loader reads
    sequential 64-row chunks and stops on the first phase-boundary chunk.  A
    boundary chunk may contain at most 16 hours beyond the phase end, but it
    must remain strictly before holdout and only rows before the phase end are
    returned to feature code.
    """

    pieces: list[pd.DataFrame] = []
    parsed_rows = 0
    parsed_max: pd.Timestamp | None = None
    end = utc(end_exclusive)
    holdout = utc(holdout_start)
    for chunk in pd.read_csv(path, chunksize=64):
        chunk["open_time"] = pd.to_datetime(chunk["open_time"], utc=True)
        parsed_rows += len(chunk)
        current_max = utc(chunk["open_time"].max())
        parsed_max = current_max if parsed_max is None else max(parsed_max, current_max)
        if chunk["open_time"].ge(holdout).any():
            raise RuntimeError(f"{path} loader parsed repository holdout")
        before = chunk.loc[chunk["open_time"].lt(end)].copy()
        if len(before):
            pieces.append(before)
        if chunk["open_time"].ge(end).any():
            break
    if not pieces:
        raise RuntimeError(f"no rows before {end} in {path}")
    frame = (
        pd.concat(pieces, ignore_index=True)
        .sort_values("open_time")
        .reset_index(drop=True)
    )
    if utc(frame["open_time"].max()) >= end:
        raise RuntimeError("bounded frame crossed phase end")
    if frame["open_time"].duplicated().any():
        raise RuntimeError(f"duplicate timestamps in {path}")
    frame["segment_id"] = frame["open_time"].diff().ne(BAR_DELTA).cumsum().astype(int)
    quality = {
        "path": path.relative_to(ROOT),
        "rows_returned": len(frame),
        "rows_physically_parsed": parsed_rows,
        "first_returned": frame["open_time"].min(),
        "last_returned": frame["open_time"].max(),
        "max_physically_parsed_time": parsed_max,
        "end_exclusive": end,
        "holdout_start": holdout,
        "holdout_rows_parsed": 0,
        "segments": int(frame["segment_id"].nunique()),
    }
    return frame, quality


def _add_native_features_one(segment: pd.DataFrame) -> pd.DataFrame:
    """Build causal 15m features inside one contiguous segment.

    Reads current/prior OHLCV only.  Maximum windows are 96 prior bars for
    normalization and 48 bars for close location.  All normalizing medians
    are shifted one bar so the current observation does not set its own base.
    """

    out = segment.copy()
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    close = out["close"].astype(float)
    volume = out["volume"].astype(float)
    hl2 = (high + low) / 2.0
    out["atr"] = pine_rma(_true_range(out), 14)
    atr = out["atr"].astype(float)
    out["ema30"] = hl2.ewm(span=30, adjust=False, min_periods=30).mean()
    out["sma60"] = hl2.rolling(60, min_periods=60).mean()
    out["ema30_slope4"] = out["ema30"] - out["ema30"].shift(4)
    out["volume_ratio96"] = _safe_div(
        volume, volume.shift(1).rolling(96, min_periods=48).median()
    )
    signed_volume = np.sign(close.diff()).fillna(0.0) * volume
    out["volume_balance12"] = _safe_div(
        signed_volume.rolling(12, min_periods=12).sum(),
        volume.rolling(12, min_periods=12).sum(),
    )
    out["atr_ratio96"] = _safe_div(
        atr, atr.shift(1).rolling(96, min_periods=48).median()
    )
    bb_width = 4.0 * close.rolling(20, min_periods=20).std(ddof=0)
    out["bb_width_ratio96"] = _safe_div(
        bb_width, bb_width.shift(1).rolling(96, min_periods=48).median()
    )
    path24 = close.diff().abs().rolling(24, min_periods=24).sum()
    out["efficiency24_signed"] = _safe_div(close - close.shift(24), path24)
    high48 = high.rolling(48, min_periods=48).max()
    low48 = low.rolling(48, min_periods=48).min()
    out["close_range48_long"] = _safe_div(close - low48, high48 - low48)
    out["close_range48_short"] = _safe_div(high48 - close, high48 - low48)
    return out.replace([np.inf, -np.inf], np.nan)


def add_native_features(frame: pd.DataFrame) -> pd.DataFrame:
    parts = [
        _add_native_features_one(part.copy())
        for _, part in frame.groupby("segment_id", sort=True)
    ]
    return pd.concat(parts).sort_index().reset_index(drop=True)


def _aggregate_complete(
    frame: pd.DataFrame,
    *,
    rule: str,
    expected_bars: int,
    ema_length: int,
    sma_length: int,
    slope_bars: int,
    return_bars: int,
) -> pd.DataFrame:
    """Aggregate exact complete 15m groups and expose them at group close."""

    rows: list[pd.DataFrame] = []
    delta = pd.Timedelta(rule)
    for segment_id, part in frame.groupby("segment_id", sort=True):
        indexed = part.set_index("open_time").sort_index()
        agg = indexed.resample(rule, label="left", closed="left", origin="epoch").agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
            bars=("close", "count"),
        )
        agg = agg.loc[agg["bars"].eq(expected_bars)].copy()
        if agg.empty:
            continue
        agg["available_time"] = agg.index + delta
        agg["source_segment_id"] = int(segment_id)
        rows.append(agg.reset_index().rename(columns={"open_time": "bucket_open"}))
    if not rows:
        raise RuntimeError(f"no complete {rule} bars")
    aggregate = pd.concat(rows, ignore_index=True).sort_values("bucket_open")
    featured: list[pd.DataFrame] = []
    for _, part in aggregate.groupby("source_segment_id", sort=True):
        part = part.copy()
        hl2 = (part["high"].astype(float) + part["low"].astype(float)) / 2.0
        part["atr"] = pine_rma(_true_range(part), 14)
        part["fast"] = hl2.ewm(
            span=ema_length, adjust=False, min_periods=ema_length
        ).mean()
        part["slow"] = hl2.rolling(sma_length, min_periods=sma_length).mean()
        part["fast_slope"] = part["fast"] - part["fast"].shift(slope_bars)
        part["return_move"] = part["close"] - part["close"].shift(return_bars)
        featured.append(part)
    return pd.concat(featured, ignore_index=True).sort_values("available_time")


def _merge_native(
    output: pd.DataFrame,
    market: pd.DataFrame,
    *,
    prefix: str,
) -> pd.DataFrame:
    columns = [
        "open_time",
        "atr",
        "ema30",
        "sma60",
        "ema30_slope4",
        "volume_ratio96",
        "volume_balance12",
        "atr_ratio96",
        "bb_width_ratio96",
        "efficiency24_signed",
        "close_range48_long",
        "close_range48_short",
    ]
    rename = {
        column: f"{prefix}_{column}" for column in columns if column != "open_time"
    }
    merged = output.merge(
        market[columns].rename(columns=rename),
        left_on="signal_time",
        right_on="open_time",
        how="left",
        validate="many_to_one",
    ).drop(columns="open_time")
    if merged[f"{prefix}_atr"].isna().all():
        raise RuntimeError(f"{prefix} native feature alignment failed")
    return merged


def _merge_htf(
    output: pd.DataFrame,
    aggregate: pd.DataFrame,
    *,
    prefix: str,
    tolerance: pd.Timedelta,
) -> pd.DataFrame:
    columns = ["available_time", "atr", "fast", "slow", "fast_slope", "return_move"]
    rename = {
        column: f"{prefix}_{column}" for column in columns if column != "available_time"
    }
    left = output.copy()
    left["_order"] = np.arange(len(left))
    merged = pd.merge_asof(
        left.sort_values("decision_time"),
        aggregate[columns].rename(columns=rename).sort_values("available_time"),
        left_on="decision_time",
        right_on="available_time",
        direction="backward",
        tolerance=tolerance,
    ).sort_values("_order")
    age = (merged["decision_time"] - merged["available_time"]).dt.total_seconds() / 60.0
    merged[f"{prefix}_age_minutes"] = age
    if merged[f"{prefix}_fast"].isna().all():
        raise RuntimeError(f"{prefix} higher-timeframe feature alignment failed")
    return merged.drop(columns=["_order", "available_time"])


def _load_ledger(config: Mapping[str, Any], phase: str) -> pd.DataFrame:
    source_key = "development_ledger" if phase == "selection" else "audit_ledger"
    source = config["sources"][source_key]
    path = ROOT / str(source["path"])
    if sha256_file(path) != str(source["sha256"]):
        raise RuntimeError(f"{source_key} hash drift")
    ledger = pd.read_csv(path)
    for column in ("signal_time", "entry_time", "exit_time"):
        ledger[column] = pd.to_datetime(ledger[column], utc=True)
    split = config["splits"]
    if phase == "selection":
        start = utc(split["development_start_inclusive"])
        end = utc(split["development_end_exclusive"])
    else:
        start = utc(split["audit_start_inclusive"])
        end = utc(split["audit_end_exclusive"])
    if not bool(
        ledger["signal_time"].ge(start).all() and ledger["signal_time"].lt(end).all()
    ):
        raise RuntimeError(f"{phase} ledger is outside its registered time box")
    if ledger["setup_id"].duplicated().any():
        raise RuntimeError(f"duplicate setup ids in {phase} ledger")
    return ledger.sort_values("signal_time").reset_index(drop=True)


def _attach_axes(output: pd.DataFrame, config: Mapping[str, Any]) -> pd.DataFrame:
    """Convert causal measurements into the six preregistered binary axes."""

    direction = output["direction"].to_numpy(dtype=float)
    for prefix in ("eth_1h", "eth_4h", "btc_1h"):
        atr = output[f"{prefix}_atr"].astype(float).replace(0.0, np.nan)
        output[f"{prefix}_spread_dir_atr"] = (
            direction * (output[f"{prefix}_fast"] - output[f"{prefix}_slow"]) / atr
        )
        output[f"{prefix}_slope_dir_atr"] = (
            direction * output[f"{prefix}_fast_slope"] / atr
        )
        output[f"{prefix}_return_dir_atr"] = (
            direction * output[f"{prefix}_return_move"] / atr
        )

    btc_atr = output["btc_atr"].astype(float).replace(0.0, np.nan)
    output["btc_15m_spread_dir_atr"] = (
        direction * (output["btc_ema30"] - output["btc_sma60"]) / btc_atr
    )
    output["btc_15m_slope_dir_atr"] = direction * output["btc_ema30_slope4"] / btc_atr
    output["eth_volume_ratio96"] = output["eth_volume_ratio96"].astype(float)
    output["eth_volume_balance12_dir"] = direction * output[
        "eth_volume_balance12"
    ].astype(float)
    output["eth_atr_ratio96"] = output["eth_atr_ratio96"].astype(float)
    output["eth_bb_width_ratio96"] = output["eth_bb_width_ratio96"].astype(float)
    output["eth_efficiency24_dir"] = direction * output[
        "eth_efficiency24_signed"
    ].astype(float)
    output["eth_close_range48_dir"] = np.where(
        direction > 0,
        output["eth_close_range48_long"],
        output["eth_close_range48_short"],
    )

    output["axis_eth_1h"] = (
        output[
            [
                "eth_1h_spread_dir_atr",
                "eth_1h_slope_dir_atr",
                "eth_1h_return_dir_atr",
            ]
        ]
        .gt(0.0)
        .sum(axis=1)
        .ge(2)
    )
    output["axis_eth_4h"] = output["eth_4h_spread_dir_atr"].gt(0.0) & output[
        "eth_4h_slope_dir_atr"
    ].ge(0.0)
    output["axis_btc_stack"] = (
        output["btc_15m_spread_dir_atr"].gt(0.0)
        & output["btc_1h_spread_dir_atr"].gt(0.0)
        & (
            output["btc_15m_slope_dir_atr"].ge(0.0)
            | output["btc_1h_slope_dir_atr"].ge(0.0)
        )
    )
    feature = config["feature_contract"]
    output["axis_participation"] = output["eth_volume_ratio96"].ge(
        float(feature["participation"]["volume_ratio_min"])
    ) & output["eth_volume_balance12_dir"].ge(
        float(feature["participation"]["directional_volume_balance_min"])
    )
    output["axis_expansion"] = output["eth_atr_ratio96"].ge(
        float(feature["expansion"]["atr_ratio_min"])
    ) & output["eth_bb_width_ratio96"].ge(
        float(feature["expansion"]["bb_width_ratio_min"])
    )
    output["axis_structure"] = (
        output["eth_efficiency24_dir"].ge(
            float(feature["structure"]["directional_efficiency_min"])
        )
        & output["eth_close_range48_dir"].ge(
            float(feature["structure"]["directional_close_range_min"])
        )
        & output["ma_side_flips_24"].le(
            float(feature["structure"]["ma_side_flips_24_max"])
        )
    )
    axes = [
        "axis_eth_1h",
        "axis_eth_4h",
        "axis_btc_stack",
        "axis_participation",
        "axis_expansion",
        "axis_structure",
    ]
    output["axis_count"] = output[axes].fillna(False).astype(int).sum(axis=1)
    gates = {
        "eth_1h": output["axis_eth_1h"],
        "eth_4h": output["axis_eth_4h"],
        "btc_stack": output["axis_btc_stack"],
        "participation": output["axis_participation"],
        "expansion": output["axis_expansion"],
        "structure": output["axis_structure"],
        "eth_multitimeframe": output["axis_eth_1h"] & output["axis_eth_4h"],
        "market_stack": output["axis_eth_1h"] & output["axis_btc_stack"],
        "trend_quality": output["axis_eth_1h"] & output["axis_structure"],
        "confirmed_expansion": output["axis_eth_1h"]
        & output["axis_structure"]
        & (output["axis_participation"] | output["axis_expansion"]),
        "vote_3_of_6": output["axis_count"].ge(3),
        "vote_4_of_6": output["axis_count"].ge(4),
    }
    registered = [str(row["id"]) for row in config["candidate_gates"]]
    if registered != list(gates):
        raise RuntimeError("candidate gate order or ids drifted from preregistration")
    for gate_id, mask in gates.items():
        output[f"gate_{gate_id}"] = mask.fillna(False).astype(bool)
    return output


def build_feature_ledger(
    config: Mapping[str, Any],
    phase: str,
    *,
    mutate_after: pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Build a phase ledger from bounded ETH/BTC prefixes and completed HTF bars."""

    ledger = _load_ledger(config, phase)
    split = config["splits"]
    end = utc(
        split["development_end_exclusive"]
        if phase == "selection"
        else split["audit_end_exclusive"]
    )
    holdout = utc(config["sources"]["holdout_start"])
    eth_raw, eth_quality = _load_prefix(
        ROOT / str(config["sources"]["eth_15m"]["path"]),
        end_exclusive=end,
        holdout_start=holdout,
    )
    btc_raw, btc_quality = _load_prefix(
        ROOT / str(config["sources"]["btc_15m"]["path"]),
        end_exclusive=end,
        holdout_start=holdout,
    )
    if mutate_after is not None:
        boundary = utc(mutate_after)
        for frame in (eth_raw, btc_raw):
            mask = frame["open_time"].ge(boundary)
            for column, multiplier in (
                ("open", 1.7),
                ("high", 1.9),
                ("low", 0.4),
                ("close", 1.6),
                ("volume", 3.0),
            ):
                frame.loc[mask, column] = (
                    frame.loc[mask, column].astype(float) * multiplier
                )
    eth = add_native_features(eth_raw)
    btc = add_native_features(btc_raw)
    eth_1h = _aggregate_complete(
        eth_raw,
        rule="1h",
        expected_bars=4,
        ema_length=8,
        sma_length=21,
        slope_bars=3,
        return_bars=6,
    )
    eth_4h = _aggregate_complete(
        eth_raw,
        rule="4h",
        expected_bars=16,
        ema_length=5,
        sma_length=13,
        slope_bars=2,
        return_bars=3,
    )
    btc_1h = _aggregate_complete(
        btc_raw,
        rule="1h",
        expected_bars=4,
        ema_length=8,
        sma_length=21,
        slope_bars=3,
        return_bars=6,
    )
    output = ledger.copy()
    output["decision_time"] = output["signal_time"] + BAR_DELTA
    output = _merge_native(output, eth, prefix="eth")
    output = _merge_native(output, btc, prefix="btc")
    output = _merge_htf(
        output, eth_1h, prefix="eth_1h", tolerance=pd.Timedelta(minutes=45)
    )
    output = _merge_htf(
        output, eth_4h, prefix="eth_4h", tolerance=pd.Timedelta(minutes=225)
    )
    output = _merge_htf(
        output, btc_1h, prefix="btc_1h", tolerance=pd.Timedelta(minutes=45)
    )
    output = _attach_axes(output, config)
    signal_indices = output["signal_i"].astype(int).to_numpy()
    if signal_indices.max(initial=-1) >= len(eth):
        raise RuntimeError("parent signal index is outside bounded ETH prefix")
    aligned = eth.loc[signal_indices, "open_time"].reset_index(drop=True)
    if not bool(aligned.eq(output["signal_time"].reset_index(drop=True)).all()):
        raise RuntimeError("parent ETH signal index no longer matches timestamp")
    gate_columns = [f"gate_{row['id']}" for row in config["candidate_gates"]]
    finite = output[FEATURE_COLUMNS].replace([np.inf, -np.inf], np.nan).notna().mean()
    receipt = {
        "phase": phase,
        "events": len(output),
        "first_signal": output["signal_time"].min(),
        "last_signal": output["signal_time"].max(),
        "feature_columns": len(FEATURE_COLUMNS),
        "candidate_gates": len(gate_columns),
        "minimum_feature_finite_rate": float(finite.min()),
        "minimum_feature_finite_name": str(finite.idxmin()),
        "eth_source": eth_quality,
        "btc_source": btc_quality,
        "repository_holdout_rows_read": int(
            eth_quality["holdout_rows_parsed"] + btc_quality["holdout_rows_parsed"]
        ),
    }
    eth["trend_ma"] = eth["sma60"]
    return eth, output.reset_index(drop=True), receipt


def _causality_receipt(
    config: Mapping[str, Any],
    base: pd.DataFrame,
    *,
    phase: str,
) -> dict[str, Any]:
    boundary = utc(
        "2024-07-01T00:00:00Z" if phase == "selection" else "2025-07-01T00:00:00Z"
    )
    _, mutated, mutation_source = build_feature_ledger(
        config, phase, mutate_after=boundary
    )
    comparable = base["signal_time"].lt(boundary)
    left = base.loc[comparable, FEATURE_COLUMNS].astype(float).to_numpy()
    right = mutated.loc[comparable, FEATURE_COLUMNS].astype(float).to_numpy()
    difference = np.abs(left - right)
    finite = difference[np.isfinite(difference)]
    maximum = float(finite.max()) if len(finite) else 0.0
    passed = bool(np.allclose(left, right, equal_nan=True, rtol=0.0, atol=1e-12))
    if not passed:
        raise RuntimeError(f"future mutation changed pre-boundary features: {maximum}")
    return {
        "phase": phase,
        "mutation_boundary": boundary,
        "pre_boundary_events_compared": int(comparable.sum()),
        "feature_columns_compared": len(FEATURE_COLUMNS),
        "maximum_absolute_difference": maximum,
        "passed": passed,
        "mutated_build_holdout_rows_read": mutation_source[
            "repository_holdout_rows_read"
        ],
    }


def _fold_table(events: pd.DataFrame, folds: list[str], gate_id: str) -> pd.DataFrame:
    labels = events["entry_time"].map(fold_label)
    return pd.DataFrame(
        [
            {"gate_id": gate_id, "fold": fold, **metrics(events.loc[labels.eq(fold)])}
            for fold in folds
        ]
    )


def _tail_retention(selected: pd.DataFrame, baseline: pd.DataFrame) -> dict[str, float]:
    top_count = max(1, math.ceil(0.10 * len(baseline)))
    top = baseline.nlargest(top_count, "net_return").copy()
    selected_ids = set(selected["setup_id"].astype(str))
    kept = top["setup_id"].astype(str).isin(selected_ids)
    denominator = float(top["net_return"].clip(lower=0.0).sum())
    numerator = float(top.loc[kept, "net_return"].clip(lower=0.0).sum())
    baseline_p95 = float(baseline["net_return"].quantile(0.95) * 1e4)
    selected_p95 = (
        float(selected["net_return"].quantile(0.95) * 1e4) if len(selected) else np.nan
    )
    return {
        "baseline_top_decile_events": top_count,
        "baseline_top_decile_event_capture": float(kept.mean()),
        "baseline_top_decile_positive_pnl_capture": (
            numerator / denominator if denominator > 0.0 else np.nan
        ),
        "candidate_p95_net_bp": selected_p95,
        "baseline_p95_net_bp": baseline_p95,
        "candidate_p95_net_retention": (
            selected_p95 / baseline_p95 if baseline_p95 > 0.0 else np.nan
        ),
    }


def _score_diagnostic(events: pd.DataFrame) -> dict[str, Any]:
    target = events["net_return"].gt(0.0).astype(int)
    auc = (
        float(roc_auc_score(target, events["axis_count"]))
        if target.nunique() == 2 and events["axis_count"].nunique() > 1
        else np.nan
    )
    count = max(1, math.ceil(len(events) * 0.10))
    top = events.sort_values(["axis_count", "setup_id"], ascending=[False, True]).head(
        count
    )
    return {
        "axis_count_auc_profit": auc,
        "top_decile_events": len(top),
        "top_decile_mean_gross_bp": float(top["gross_return"].mean() * 1e4),
        "top_decile_mean_net_bp": float(top["net_return"].mean() * 1e4),
        "top_decile_win_rate": float(top["net_return"].gt(0.0).mean()),
        "tie_break": "axis_count descending then setup_id ascending",
    }


def _familywise_permutation_p(
    events: pd.DataFrame,
    gate_ids: list[str],
    *,
    resamples: int,
    seed: int,
) -> dict[str, float]:
    """Max-statistic p-values for all fixed selection masks.

    The null permutes V16 net outcomes across the unchanged causal masks.  The
    statistic is selected-mean minus all-event mean in basis points.  Taking
    the maximum over all masks on every permutation controls selection across
    the registered family.
    """

    values = events["net_return"].to_numpy(dtype=float) * 1e4
    masks = np.vstack(
        [events[f"gate_{gate_id}"].to_numpy(dtype=bool) for gate_id in gate_ids]
    )
    counts = masks.sum(axis=1).astype(float)
    weights = masks.astype(float)
    observed = np.sum(weights * values[None, :], axis=1) / counts - float(values.mean())
    rng = np.random.default_rng(seed)
    exceed = np.zeros(len(gate_ids), dtype=int)
    done = 0
    chunk = 250
    while done < resamples:
        current = min(chunk, resamples - done)
        orders = np.argsort(rng.random((current, len(values))), axis=1)
        permuted = values[orders]
        statistics = np.sum(
            permuted[:, None, :] * weights[None, :, :], axis=2
        ) / counts - float(values.mean())
        maximum = statistics.max(axis=1)
        exceed += (maximum[:, None] >= observed[None, :] - 1e-12).sum(axis=0)
        done += current
    return {
        gate_id: float((exceed[index] + 1) / (resamples + 1))
        for index, gate_id in enumerate(gate_ids)
    }


def _evaluate_variants(
    events: pd.DataFrame,
    config: Mapping[str, Any],
    *,
    phase: str,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    split = config["splits"]
    folds = list(
        map(
            str,
            split["development_folds"]
            if phase == "selection"
            else split["audit_folds"],
        )
    )
    baseline_metrics = metrics(events)
    baseline_folds = _fold_table(events, folds, "v16_all")
    baseline_fold_map = baseline_folds.set_index("fold")["mean_net_bp"].to_dict()
    gate_ids = [str(row["id"]) for row in config["candidate_gates"]]
    p_values = (
        _familywise_permutation_p(
            events,
            gate_ids,
            resamples=int(config["selection"]["familywise_permutation_resamples"]),
            seed=int(config["selection"]["seed"]),
        )
        if phase == "selection"
        else {gate_id: np.nan for gate_id in gate_ids}
    )
    rows: list[dict[str, Any]] = []
    fold_rows = [baseline_folds]
    for gate_id in gate_ids:
        selected = events.loc[events[f"gate_{gate_id}"].fillna(False)].copy()
        summary = metrics(selected)
        table = _fold_table(selected, folds, gate_id)
        fold_rows.append(table)
        means = table["mean_net_bp"].to_numpy(dtype=float)
        counts = table["events"].to_numpy(dtype=int)
        finite = bool(len(means) and np.isfinite(means).all())
        row = {
            "gate_id": gate_id,
            **summary,
            **_tail_retention(selected, events),
            "selection_rate": float(len(selected) / len(events)),
            "candidate_minus_baseline_mean_net_bp": float(
                summary["mean_net_bp"] - baseline_metrics["mean_net_bp"]
            ),
            "minimum_fold_events": int(counts.min()) if len(counts) else 0,
            "positive_absolute_folds": int(np.sum(means > 0.0)) if finite else 0,
            "folds_beating_baseline": int(
                sum(
                    float(item.mean_net_bp) > float(baseline_fold_map[item.fold])
                    for item in table.itertuples(index=False)
                    if np.isfinite(float(item.mean_net_bp))
                )
            ),
            "robust_score_bp": (
                float(np.median(means) - 0.5 * np.std(means, ddof=0))
                if finite
                else np.nan
            ),
            "worst_fold_net_bp": float(np.min(means)) if finite else np.nan,
            "familywise_permutation_p": p_values[gate_id],
        }
        if phase == "selection":
            gate = config["selection"]
            checks = {
                "events_total": len(selected) >= int(gate["minimum_events_total"]),
                "events_per_fold": row["minimum_fold_events"]
                >= int(gate["minimum_events_per_fold"]),
                "selection_rate": float(gate["selection_rate_min"])
                <= row["selection_rate"]
                <= float(gate["selection_rate_max"]),
                "mean_improvement": row["candidate_minus_baseline_mean_net_bp"]
                >= float(gate["candidate_minus_baseline_mean_net_bp_min"]),
                "mean_net": row["mean_net_bp"]
                >= float(gate["candidate_mean_net_bp_min"]),
                "profit_factor": row["profit_factor"]
                >= float(gate["candidate_profit_factor_min"]),
                "positive_folds": row["positive_absolute_folds"]
                >= int(gate["positive_absolute_folds_min"]),
                "folds_beating_baseline": row["folds_beating_baseline"]
                >= int(gate["folds_beating_baseline_min"]),
                "right_tail_pnl": row["baseline_top_decile_positive_pnl_capture"]
                >= float(gate["baseline_top_decile_positive_pnl_capture_min"]),
                "p95_retention": row["candidate_p95_net_retention"]
                >= float(gate["candidate_p95_net_retention_min"]),
                "familywise_p": row["familywise_permutation_p"]
                <= float(gate["familywise_permutation_p_max"]),
            }
            row.update({f"gate_{key}": value for key, value in checks.items()})
            row["all_opening_gates_pass"] = bool(all(checks.values()))
        rows.append(row)
    return pd.DataFrame(rows), pd.concat(fold_rows, ignore_index=True), baseline_metrics


def selection_phase(config: dict[str, Any]) -> dict[str, Any]:
    for path in (CONFIG_PATH, PREREG_PATH, SCRIPT_PATH):
        _assert_head_frozen(path)
    _, events, source = build_feature_ledger(config, "selection")
    causality = _causality_receipt(config, events, phase="selection")
    variants, folds, baseline = _evaluate_variants(events, config, phase="selection")
    passing = variants.loc[variants["all_opening_gates_pass"]].copy()
    rank_columns = [
        "robust_score_bp",
        "baseline_top_decile_positive_pnl_capture",
        "mean_net_bp",
        "events",
    ]
    ranked = variants.sort_values(rank_columns, ascending=[False, False, False, False])
    diagnostic_nominee = str(ranked.iloc[0]["gate_id"])
    selected_gate = (
        str(
            passing.sort_values(
                rank_columns, ascending=[False, False, False, False]
            ).iloc[0]["gate_id"]
        )
        if len(passing)
        else None
    )
    all_pass = selected_gate is not None
    selected_or_nominee = selected_gate or diagnostic_nominee
    selected_trades = events.loc[events[f"gate_{selected_or_nominee}"]].copy()
    RESULTS.mkdir(parents=True, exist_ok=True)
    write_csv(events, RESULTS / "development_feature_ledger.csv.gz")
    write_csv(variants, RESULTS / "development_variant_summary.csv")
    write_csv(folds, RESULTS / "development_fold_metrics.csv")
    write_csv(selected_trades, RESULTS / "development_nominee_trades.csv.gz")
    write_json(RESULTS / "causality_receipt.json", causality)
    receipt = {
        "experiment_id": EXPERIMENT_ID,
        "phase": "selection",
        "status": "frozen_for_audit" if all_pass else "rejected_before_audit",
        "selected_gate_id": selected_gate,
        "diagnostic_nominee": diagnostic_nominee,
        "all_registered_gates_pass": all_pass,
        "passing_gate_ids": passing["gate_id"].astype(str).tolist(),
        "baseline": baseline,
        "score_diagnostic": _score_diagnostic(events),
        "nominee": ranked.iloc[0].to_dict(),
        "source": source,
        "causality": causality,
        "audit_rows_read": 0,
        "repository_holdout_rows_read": source["repository_holdout_rows_read"],
        "hashes": {
            "config_sha256": sha256_file(CONFIG_PATH),
            "preregistration_sha256": sha256_file(PREREG_PATH),
            "script_sha256": sha256_file(SCRIPT_PATH),
            "variant_summary_sha256": sha256_file(
                RESULTS / "development_variant_summary.csv"
            ),
        },
    }
    write_json(SELECTION_RECEIPT, receipt)
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))
    return receipt


def _failure_table(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame(columns=["failure_category", "events", "mean_net_bp"])
    net_positive = events["net_return"].gt(0.0)
    categories = np.select(
        [
            net_positive & events["gave_back_atr"].ge(2.0),
            net_positive,
            events["runner_armed"].astype(bool),
            events["gross_return"].gt(0.0),
            events["horizon_mfe_atr"].ge(2.0),
        ],
        [
            "winner_large_giveback",
            "winner_retained",
            "armed_profit_given_back",
            "gross_win_erased_by_cost",
            "early_stop_then_later_recovered",
        ],
        default="false_launch_or_other_loss",
    )
    work = events.assign(failure_category=categories)
    return (
        work.groupby("failure_category", as_index=False)
        .agg(events=("setup_id", "size"), mean_net_return=("net_return", "mean"))
        .assign(mean_net_bp=lambda x: x.pop("mean_net_return") * 1e4)
        .sort_values("failure_category")
    )


def audit_phase(config: dict[str, Any]) -> dict[str, Any]:
    for path in (CONFIG_PATH, PREREG_PATH, SCRIPT_PATH):
        _assert_head_frozen(path)
    selection = _assert_selection_committed()
    selected_gate = str(selection["selected_gate_id"])
    eth, events, source = build_feature_ledger(config, "audit")
    causality = _causality_receipt(config, events, phase="audit")
    candidate = events.loc[events[f"gate_{selected_gate}"]].copy()
    variants, folds, baseline_metrics = _evaluate_variants(
        events, config, phase="audit"
    )
    candidate_metrics = metrics(candidate)
    tail = _tail_retention(candidate, events)
    split = config["splits"]
    audit_folds = list(map(str, split["audit_folds"]))
    candidate_folds = _fold_table(candidate, audit_folds, selected_gate)
    parent = json.loads(PARENT_CONFIG_PATH.read_text(encoding="utf-8"))
    parent["matched_control"] = dict(config["matched_control"])
    controls, matched_pairs = _matched_controls(
        candidate,
        eth,
        parent,
        bank=0.10,
        start=utc(split["audit_start_inclusive"]),
        end=utc(split["audit_end_exclusive"]),
    )
    matched = matched_pairs.loc[
        matched_pairs["match_status"].eq("matched_exact")
    ].copy()
    excess = matched["paired_excess_return"].astype(float)
    matched_summary = {
        "matched_events": len(matched),
        "candidate_mean_net_bp": (
            float(matched["candidate_net_return"].mean() * 1e4)
            if len(matched)
            else np.nan
        ),
        "control_mean_net_bp": (
            float(matched["control_mean_net_return"].mean() * 1e4)
            if len(matched)
            else np.nan
        ),
        "excess_bp": float(excess.mean() * 1e4) if len(excess) else np.nan,
        "signflip_p_one_sided": (
            float(signflip_p(excess, resamples=100_000, seed=2026090518))
            if len(excess)
            else np.nan
        ),
    }
    positive_folds = int(candidate_folds["mean_net_bp"].gt(0.0).sum())
    minimum_fold_events = int(candidate_folds["events"].min())
    audit_gate = config["audit_gate"]
    improvement = float(
        candidate_metrics["mean_net_bp"] - baseline_metrics["mean_net_bp"]
    )
    checks = {
        "events_total": len(candidate) >= int(audit_gate["minimum_events_total"]),
        "events_per_fold": minimum_fold_events
        >= int(audit_gate["minimum_events_per_fold"]),
        "mean_net": candidate_metrics["mean_net_bp"]
        > float(audit_gate["mean_net_bp_gt"]),
        "profit_factor": candidate_metrics["profit_factor"]
        > float(audit_gate["profit_factor_gt"]),
        "mean_improvement": improvement
        >= float(audit_gate["candidate_minus_baseline_mean_net_bp_min"]),
        "positive_folds": positive_folds >= int(audit_gate["positive_folds_min"]),
        "matched_random_excess": matched_summary["excess_bp"]
        > float(audit_gate["matched_random_excess_bp_gt"]),
        "matched_random_p": matched_summary["signflip_p_one_sided"]
        < float(audit_gate["matched_random_signflip_p_lt"]),
        "right_tail_pnl": tail["baseline_top_decile_positive_pnl_capture"]
        >= float(audit_gate["baseline_top_decile_positive_pnl_capture_min"]),
    }
    failure = _failure_table(candidate)
    RESULTS.mkdir(parents=True, exist_ok=True)
    write_csv(events, RESULTS / "audit_feature_ledger.csv.gz")
    write_csv(candidate, RESULTS / "audit_selected_trades.csv.gz")
    write_csv(variants, RESULTS / "audit_variant_summary.csv")
    write_csv(folds, RESULTS / "audit_all_variant_fold_metrics.csv")
    write_csv(candidate_folds, RESULTS / "audit_selected_fold_metrics.csv")
    write_csv(controls, RESULTS / "audit_matched_controls.csv.gz")
    write_csv(matched_pairs, RESULTS / "audit_matched_pairs.csv")
    write_csv(failure, RESULTS / "audit_failure_mechanics.csv")
    write_json(RESULTS / "audit_causality_receipt.json", causality)
    receipt = {
        "experiment_id": EXPERIMENT_ID,
        "phase": "already_seen_transport_audit",
        "status": "research_gate_pass"
        if all(checks.values())
        else "research_gate_fail",
        "selected_gate_id": selected_gate,
        "baseline": baseline_metrics,
        "candidate": {
            **candidate_metrics,
            **tail,
            "selection_rate": float(len(candidate) / len(events)),
            "candidate_minus_baseline_mean_net_bp": improvement,
            "minimum_fold_events": minimum_fold_events,
            "positive_folds": positive_folds,
        },
        "score_diagnostic": _score_diagnostic(events),
        "matched_random": matched_summary,
        "audit_checks": checks,
        "all_audit_gates_pass": bool(all(checks.values())),
        "source": source,
        "causality": causality,
        "repository_holdout_rows_read": source["repository_holdout_rows_read"],
        "production_eligible": False,
        "active_forward_tradingview_or_live_changed": False,
        "selection_receipt_sha256": sha256_file(SELECTION_RECEIPT),
        "config_sha256": sha256_file(CONFIG_PATH),
        "script_sha256": sha256_file(SCRIPT_PATH),
    }
    write_json(RESULTS / "audit_receipt.json", receipt)
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True, choices=("selection", "audit"))
    args = parser.parse_args()
    config = load_config()
    if args.phase == "selection":
        selection_phase(config)
    else:
        audit_phase(config)


if __name__ == "__main__":
    main()
