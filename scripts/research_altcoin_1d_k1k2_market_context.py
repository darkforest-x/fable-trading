#!/usr/bin/env python3
"""Test causal market context on the frozen altcoin daily K1->K2 strategy.

Daily source OHLCV is aggregated from exactly 96 completed 15-minute UTC bars.
At a signal-day close ``t``, the only new features are: contemporaneous market
breadth and its five-day change; BTC/ETH EMA13/SMA34 alignment and trailing
20-day return scaled by ATR14; and direction-adjusted cross-sectional ranks of
the altcoin's trailing 20-day return and path efficiency.  Every rolling input
ends at ``t`` and the immutable parent entry remains the next UTC daily open.

The already-viewed 52-symbol universe is development-only. Confirmation A is a
hash-partitioned set of symbols never used by V1/V2; confirmation B remains
sealed.  The bounded 15-minute parser stops before 2026-05-01 and cannot reach
the repository holdout beginning 2026-05-04.  This script does not train or
promote a model, write TradingView, change ACTIVE/forward state, or touch orders.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from scripts import research_altcoin_1d_k1k2_episode_runner as signal_parent
from scripts import research_altcoin_1d_k1k2_risk_repair as execution_parent
from scripts.backtest_two_key_candle_pine_v8_btc_1h import signflip_p
from scripts.research_btcusdtp_15m_ma_state_trend import json_value, write_csv, write_json
from scripts.research_pine_eth_15m import load_development_frame, sha256_bounded_frame
from scripts.research_two_key_candle_ma_retest_1h import sha256_file

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = Path(__file__).resolve()
DAY = pd.Timedelta(days=1)


def utc(value: object) -> pd.Timestamp:
    """Return a timezone-aware UTC timestamp."""

    return signal_parent.utc(value)


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_head_frozen(path: Path) -> None:
    relative = path.relative_to(ROOT).as_posix()
    subprocess.run(
        ["git", "ls-files", "--error-unmatch", relative],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    expected = subprocess.check_output(["git", "show", f"HEAD:{relative}"], cwd=ROOT)
    if hashlib.sha256(expected).digest() != hashlib.sha256(path.read_bytes()).digest():
        raise RuntimeError(f"{relative} differs from frozen HEAD")


def _assert_hash(path: Path, expected: str, label: str) -> None:
    _assert_head_frozen(path)
    if sha256_file(path) != str(expected):
        raise RuntimeError(f"{label} hash drifted")


def _load_contracts(config: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    parents = config["parents"]
    signal_path = ROOT / str(parents["signal_config_path"])
    execution_path = ROOT / str(parents["execution_config_path"])
    receipt_path = ROOT / str(parents["execution_receipt_path"])
    manifest_path = ROOT / str(parents["universe_manifest_path"])
    _assert_hash(signal_path, parents["signal_config_sha256"], "signal config")
    _assert_hash(execution_path, parents["execution_config_sha256"], "execution config")
    _assert_hash(receipt_path, parents["execution_receipt_sha256"], "execution receipt")
    _assert_hash(manifest_path, parents["universe_manifest_sha256"], "universe manifest")
    signal_config = load_config(signal_path)
    execution_config = load_config(execution_path)
    receipt = load_config(receipt_path)
    if dict(receipt.get("selected_params", {})) != dict(parents["fixed_execution_params"]):
        raise RuntimeError("frozen V2 execution parameters do not match V3")
    manifest = load_config(manifest_path)
    _validate_manifest(manifest)
    return signal_config, execution_config, manifest


def _validate_manifest(manifest: Mapping[str, Any]) -> None:
    a = set(manifest["confirmation_a"])
    b = set(manifest["sealed_confirmation_b"])
    excluded = set(manifest["development_symbols_excluded_from_holdbacks"])
    if a & b or a & excluded or b & excluded:
        raise RuntimeError("universe partitions overlap")
    partition = manifest["partition"]
    if len(a) != int(partition["confirmation_a_count"]):
        raise RuntimeError("confirmation A count drifted")
    if len(b) != int(partition["sealed_b_count"]):
        raise RuntimeError("sealed B count drifted")
    if not bool(manifest["created_without_price_or_outcome_inspection"]):
        raise RuntimeError("manifest lacks outcome-free construction receipt")


def _execution_adapter(
    config: Mapping[str, Any], execution_config: Mapping[str, Any]
) -> dict[str, Any]:
    adapted = deepcopy(dict(execution_config))
    adapted["splits"] = deepcopy(dict(config["splits"]))
    adapted["matched_control"] = deepcopy(dict(config["matched_control"]))
    adapted["selection"]["minimums"] = deepcopy(dict(config["selection"]["minimums"]))
    adapted["selection"]["p95_raw_net_r_retention_min"] = float(
        config["selection"]["p95_raw_net_r_retention_min"]
    )
    adapted["confirmation_minimums"] = deepcopy(dict(config["confirmation_minimums"]))
    return adapted


def _frame_sha256(frame: pd.DataFrame, columns: Iterable[str]) -> str:
    payload = frame[list(columns)].to_csv(index=False, float_format="%.12g").encode()
    return hashlib.sha256(payload).hexdigest()


def _load_one_source(path: Path, config: Mapping[str, Any], end: pd.Timestamp) -> pd.DataFrame:
    contract = config["source_contract"]
    return load_development_frame(
        path,
        safe_end=end,
        holdout_start=utc(contract["holdout_start"]),
        chunksize=int(contract["parser_chunksize"]),
    ).copy()


def load_manifest_partition(
    config: Mapping[str, Any],
    manifest: Mapping[str, Any],
    *,
    partition: str,
    end_exclusive: pd.Timestamp,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame, dict[str, Any]]:
    """Load only one explicit manifest partition and stop before holdout.

    Each source path is parsed only until ``end_exclusive``. Multiple source
    segments for one symbol are concatenated without filling the intervening
    calendar gap; ``aggregate_complete_utc_days`` creates a new segment id.
    """

    if partition != "confirmation_a":
        raise RuntimeError("V3 may load confirmation A only; B is sealed")
    end = utc(end_exclusive)
    contract = config["source_contract"]
    if end > utc(contract["safe_end_exclusive"]):
        raise RuntimeError("requested end exceeds safe pre-holdout end")
    records = manifest[partition]
    sealed_paths = {
        source
        for item in manifest["sealed_confirmation_b"].values()
        for source in item["sources"]
    }
    opened_paths: set[str] = set()
    frames: dict[str, pd.DataFrame] = {}
    quality_rows: list[dict[str, Any]] = []
    for symbol, record in sorted(records.items()):
        pieces: list[pd.DataFrame] = []
        path_receipts: list[dict[str, Any]] = []
        for relative in record["sources"]:
            if relative in sealed_paths:
                raise RuntimeError(f"A source overlaps sealed B: {relative}")
            opened_paths.add(str(relative))
            try:
                raw = _load_one_source(ROOT / str(relative), config, end)
            except ValueError as error:
                if not str(error).startswith("no development rows in "):
                    raise
                path_receipts.append(
                    {
                        "path": relative,
                        "rows": 0,
                        "prefix_sha256": None,
                        "status": "not_listed_yet",
                    }
                )
                continue
            if bool(raw["open_time"].ge(utc(contract["holdout_start"])).any()):
                raise RuntimeError(f"{symbol} materialized repository holdout")
            pieces.append(raw)
            path_receipts.append(
                {
                    "path": relative,
                    "rows": int(len(raw)),
                    "prefix_sha256": sha256_bounded_frame(raw),
                    "status": "loaded_preholdout_prefix",
                }
            )
        if not pieces:
            quality_rows.append(
                {
                    "symbol": symbol,
                    "cohort": record["cohort"],
                    "paths": json.dumps(record["sources"], separators=(",", ":")),
                    "path_receipts": json.dumps(path_receipts, sort_keys=True, separators=(",", ":")),
                    "source_rows_read": 0,
                    "complete_days": 0,
                    "discarded_days": 0,
                    "first_daily_bar": pd.NaT,
                    "last_daily_bar": pd.NaT,
                    "daily_prefix_sha256": None,
                    "phase_source_status": "not_listed_yet",
                    "holdout_rows_read": 0,
                }
            )
            continue
        raw = pd.concat(pieces, ignore_index=True).sort_values("open_time", kind="mergesort")
        if raw["open_time"].duplicated().any():
            raise RuntimeError(f"overlapping source segments for {symbol}")
        daily, aggregate_quality = signal_parent.aggregate_complete_utc_days(
            raw,
            expected_bars=int(contract["intraday_bars_per_complete_day"]),
        )
        daily = daily[daily["open_time"].lt(end)].reset_index(drop=True)
        eligible = len(daily) >= int(contract["minimum_daily_history_bars"])
        if eligible:
            frames[str(symbol)] = daily
        quality_rows.append(
            {
                "symbol": symbol,
                "cohort": record["cohort"],
                "paths": json.dumps(record["sources"], separators=(",", ":")),
                "path_receipts": json.dumps(path_receipts, sort_keys=True, separators=(",", ":")),
                "source_rows_read": int(len(raw)),
                **aggregate_quality,
                "first_daily_bar": daily["open_time"].iloc[0] if len(daily) else pd.NaT,
                "last_daily_bar": daily["open_time"].iloc[-1] if len(daily) else pd.NaT,
                "daily_prefix_sha256": _frame_sha256(
                    daily, ["open_time", "open", "high", "low", "close", "volume"]
                )
                if len(daily)
                else None,
                "phase_source_status": "eligible" if eligible else "insufficient_history",
                "holdout_rows_read": 0,
            }
        )
    if opened_paths & sealed_paths:
        raise RuntimeError("sealed B path was opened")
    quality = pd.DataFrame(quality_rows)
    summary = {
        "configured_symbols": int(len(records)),
        "eligible_symbols": int(len(frames)),
        "source_rows_read": int(quality["source_rows_read"].sum()),
        "complete_daily_bars": int(quality["complete_days"].sum()),
        "discarded_days": int(quality["discarded_days"].sum()),
        "first_daily_bar": quality["first_daily_bar"].min(),
        "last_daily_bar": quality["last_daily_bar"].max(),
        "bounded_end_exclusive": end,
        "holdout_start": utc(contract["holdout_start"]),
        "repository_holdout_rows_read": int(quality["holdout_rows_read"].sum()),
        "sealed_b_rows_read": 0,
        "opened_source_paths": int(len(opened_paths)),
    }
    if summary["repository_holdout_rows_read"] != 0:
        raise RuntimeError("confirmation loader crossed repository holdout")
    return frames, quality, summary


def load_reference_markets(
    config: Mapping[str, Any], *, end_exclusive: pd.Timestamp
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    end = utc(end_exclusive)
    contract = config["source_contract"]
    frames: dict[str, pd.DataFrame] = {}
    rows: list[dict[str, Any]] = []
    for symbol, relative in sorted(config["reference_markets"].items()):
        raw = _load_one_source(ROOT / str(relative), config, end)
        if bool(raw["open_time"].ge(utc(contract["holdout_start"])).any()):
            raise RuntimeError(f"reference {symbol} crossed repository holdout")
        daily, quality = signal_parent.aggregate_complete_utc_days(
            raw, expected_bars=int(contract["intraday_bars_per_complete_day"])
        )
        daily = daily[daily["open_time"].lt(end)].reset_index(drop=True)
        if len(daily) < int(contract["minimum_daily_history_bars"]):
            raise RuntimeError(f"reference {symbol} lacks daily history")
        frames[symbol] = daily
        rows.append(
            {
                "symbol": symbol,
                "path": relative,
                "source_rows_read": int(len(raw)),
                "source_prefix_sha256": sha256_bounded_frame(raw),
                **quality,
                "last_daily_bar": daily["open_time"].iloc[-1],
                "holdout_rows_read": 0,
            }
        )
    return frames, pd.DataFrame(rows)


def _trailing_return_and_efficiency(
    frame: pd.DataFrame, *, window: int
) -> tuple[pd.Series, pd.Series]:
    returns = pd.Series(np.nan, index=frame.index, dtype=float)
    efficiency = pd.Series(np.nan, index=frame.index, dtype=float)
    for _, indices in frame.groupby("segment_id", sort=False).groups.items():
        idx = pd.Index(indices)
        close = frame.loc[idx, "close"].astype(float)
        ret = close / close.shift(window) - 1.0
        distance = close - close.shift(window)
        path = close.diff().abs().rolling(window, min_periods=window).sum()
        eff = distance / path.replace(0.0, np.nan)
        returns.loc[idx] = ret.to_numpy(dtype=float)
        efficiency.loc[idx] = eff.to_numpy(dtype=float)
    return returns, efficiency


def build_context_panel(
    frames: Mapping[str, pd.DataFrame],
    reference_frames: Mapping[str, pd.DataFrame],
    config: Mapping[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Create causal same-day breadth, major-regime and cross-sectional fields.

    For each output row at daily close ``t``, this function reads only target
    and reference OHLCV at ``t`` or earlier. Cross-sectional ranks use other
    symbols at the same completed close, which is available before next-open
    entry. Rolling returns and efficiencies reset at source gaps.
    """

    spec = config["context_features"]
    return_window = int(spec["relative_return_window"])
    efficiency_window = int(spec["relative_efficiency_window"])
    rows: list[pd.DataFrame] = []
    for symbol, frame in sorted(frames.items()):
        ret, _ = _trailing_return_and_efficiency(frame, window=return_window)
        _, efficiency = _trailing_return_and_efficiency(
            frame, window=efficiency_window
        )
        valid = frame[["close", "fast_ma", "slow_ma", "fast_slope3_atr"]].notna().all(axis=1)
        current = pd.DataFrame(
            {
                "symbol": symbol,
                "open_time": pd.to_datetime(frame["open_time"], utc=True),
                "return20": ret,
                "efficiency20": efficiency,
                "trend_valid": valid,
                "trend_up": valid
                & frame["close"].gt(frame["slow_ma"])
                & frame["fast_ma"].gt(frame["slow_ma"])
                & frame["fast_slope3_atr"].gt(0.0),
                "trend_down": valid
                & frame["close"].lt(frame["slow_ma"])
                & frame["fast_ma"].lt(frame["slow_ma"])
                & frame["fast_slope3_atr"].lt(0.0),
            }
        )
        rows.append(current)
    panel = pd.concat(rows, ignore_index=True)
    panel["return_rank"] = panel.groupby("open_time", sort=True)["return20"].rank(
        pct=True, method="average"
    )
    panel["efficiency_rank"] = panel.groupby("open_time", sort=True)["efficiency20"].rank(
        pct=True, method="average"
    )
    panel["cross_section_count"] = panel.groupby("open_time", sort=True)["return20"].transform(
        "count"
    )
    breadth = (
        panel.groupby("open_time", as_index=False, sort=True)
        .agg(
            breadth_constituents=("trend_valid", "sum"),
            trend_up_count=("trend_up", "sum"),
            trend_down_count=("trend_down", "sum"),
        )
        .sort_values("open_time", kind="mergesort")
    )
    denominator = breadth["breadth_constituents"].replace(0, np.nan).astype(float)
    breadth["up_breadth"] = breadth["trend_up_count"] / denominator
    breadth["down_breadth"] = breadth["trend_down_count"] / denominator
    lag = int(spec["breadth_change_lag"])
    breadth["up_breadth_change5"] = breadth["up_breadth"] - breadth["up_breadth"].shift(lag)
    breadth["down_breadth_change5"] = breadth["down_breadth"] - breadth["down_breadth"].shift(lag)
    minimum = int(spec["minimum_breadth_constituents"])
    low_coverage = breadth["breadth_constituents"].lt(minimum)
    for column in (
        "up_breadth",
        "down_breadth",
        "up_breadth_change5",
        "down_breadth_change5",
    ):
        breadth.loc[low_coverage, column] = np.nan

    major_rows: list[pd.DataFrame] = []
    major_window = int(spec["major_return_window"])
    signal_config = load_config(ROOT / str(config["parents"]["signal_config_path"]))
    for symbol, daily in sorted(reference_frames.items()):
        frame = signal_parent.build_profile(daily, signal_config, "ema13_sma34")
        ret, _ = _trailing_return_and_efficiency(frame, window=major_window)
        atr_fraction = frame["atr"].astype(float) / frame["close"].astype(float)
        valid = frame[["close", "fast_ma", "slow_ma", "fast_slope3_atr", "atr"]].notna().all(axis=1)
        major_rows.append(
            pd.DataFrame(
                {
                    "symbol": symbol,
                    "open_time": pd.to_datetime(frame["open_time"], utc=True),
                    "major_valid": valid,
                    "major_up": valid
                    & frame["close"].gt(frame["slow_ma"])
                    & frame["fast_ma"].gt(frame["slow_ma"])
                    & frame["fast_slope3_atr"].gt(0.0),
                    "major_down": valid
                    & frame["close"].lt(frame["slow_ma"])
                    & frame["fast_ma"].lt(frame["slow_ma"])
                    & frame["fast_slope3_atr"].lt(0.0),
                    "major_momentum_atr": ret / atr_fraction.replace(0.0, np.nan),
                }
            )
        )
    majors = pd.concat(major_rows, ignore_index=True)
    major_daily = (
        majors.groupby("open_time", as_index=False, sort=True)
        .agg(
            major_constituents=("major_valid", "sum"),
            major_up_count=("major_up", "sum"),
            major_down_count=("major_down", "sum"),
            major_momentum_atr=("major_momentum_atr", "mean"),
        )
        .sort_values("open_time", kind="mergesort")
    )
    major_denominator = major_daily["major_constituents"].replace(0, np.nan).astype(float)
    major_daily["major_up_alignment"] = major_daily["major_up_count"] / major_denominator
    major_daily["major_down_alignment"] = major_daily["major_down_count"] / major_denominator
    major_daily.loc[major_daily["major_constituents"].lt(2), [
        "major_up_alignment",
        "major_down_alignment",
        "major_momentum_atr",
    ]] = np.nan
    panel = panel.merge(breadth, on="open_time", how="left", validate="many_to_one")
    panel = panel.merge(major_daily, on="open_time", how="left", validate="many_to_one")
    return panel.sort_values(["symbol", "open_time"], kind="mergesort").reset_index(drop=True), major_daily


def directional_context(
    row: Mapping[str, Any], direction: int, config: Mapping[str, Any]
) -> dict[str, Any]:
    """Convert direction-neutral causal fields into [0,1] alignment scores."""

    spec = config["context_features"]
    direction = 1 if int(direction) > 0 else -1
    breadth_level = float(row["up_breadth"] if direction > 0 else row["down_breadth"])
    breadth_change = float(
        row["up_breadth_change5"] if direction > 0 else row["down_breadth_change5"]
    )
    change_score = float(
        np.clip(
            0.5 + breadth_change / float(spec["breadth_change_full_scale"]),
            0.0,
            1.0,
        )
    )
    breadth_weights = spec["breadth_score_weights"]
    breadth_score = float(
        float(breadth_weights["level"]) * breadth_level
        + float(breadth_weights["change"]) * change_score
    )
    major_alignment = float(
        row["major_up_alignment"] if direction > 0 else row["major_down_alignment"]
    )
    signed_major_momentum = direction * float(row["major_momentum_atr"])
    major_momentum_score = float(
        np.clip(
            0.5 + np.clip(signed_major_momentum, -4.0, 4.0) / 8.0,
            0.0,
            1.0,
        )
    )
    major_weights = spec["major_score_weights"]
    major_score = float(
        float(major_weights["alignment"]) * major_alignment
        + float(major_weights["atr_scaled_momentum"]) * major_momentum_score
    )
    n = float(row["cross_section_count"])
    correction = 1.0 / n if np.isfinite(n) and n > 0 else np.nan
    return_rank = float(row["return_rank"])
    efficiency_rank = float(row["efficiency_rank"])
    directional_return_rank = return_rank if direction > 0 else 1.0 - return_rank + correction
    directional_efficiency_rank = efficiency_rank if direction > 0 else 1.0 - efficiency_rank + correction
    directional_return_rank = float(np.clip(directional_return_rank, 0.0, 1.0))
    directional_efficiency_rank = float(np.clip(directional_efficiency_rank, 0.0, 1.0))
    relative_weights = spec["relative_score_weights"]
    relative_score = float(
        float(relative_weights["return_rank"]) * directional_return_rank
        + float(relative_weights["efficiency_rank"]) * directional_efficiency_rank
    )
    context_weights = spec["context_score_weights"]
    context_mean = float(
        float(context_weights["breadth"]) * breadth_score
        + float(context_weights["major"]) * major_score
        + float(context_weights["relative"]) * relative_score
    )
    values = np.asarray(
        [
            breadth_level,
            breadth_change,
            breadth_score,
            major_alignment,
            signed_major_momentum,
            major_score,
            directional_return_rank,
            directional_efficiency_rank,
            relative_score,
            context_mean,
        ],
        dtype=float,
    )
    breadth_constituents = float(row["breadth_constituents"])
    cross_section_count = float(row["cross_section_count"])
    return {
        "context_available": bool(np.isfinite(values).all()),
        "context_breadth_constituents": (
            int(breadth_constituents) if np.isfinite(breadth_constituents) else 0
        ),
        "context_cross_section_count": (
            int(cross_section_count) if np.isfinite(cross_section_count) else 0
        ),
        "context_breadth_level": breadth_level,
        "context_breadth_change5": breadth_change,
        "context_breadth_score": breadth_score,
        "context_major_alignment": major_alignment,
        "context_major_momentum_atr": signed_major_momentum,
        "context_major_score": major_score,
        "context_relative_return_rank": directional_return_rank,
        "context_relative_efficiency_rank": directional_efficiency_rank,
        "context_relative_score": relative_score,
        "context_mean": context_mean,
        "context_floor": float(min(breadth_score, major_score, relative_score)),
    }


def build_phase_inputs(
    universe: Mapping[str, pd.DataFrame],
    reference_frames: Mapping[str, pd.DataFrame],
    signal_config: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    phase: str,
    cohort_by_symbol: Mapping[str, str] | None = None,
) -> tuple[
    dict[str, pd.DataFrame],
    dict[str, pd.DataFrame],
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
]:
    params = dict(config["parents"]["fixed_signal_params"])
    split = config["splits"][phase]
    start = utc(split["start_inclusive"])
    end = utc(split["end_exclusive"])
    frames = {
        symbol: signal_parent.build_profile(daily, signal_config, str(params["ma_profile"]))
        for symbol, daily in sorted(universe.items())
    }
    panel, _ = build_context_panel(frames, reference_frames, config)
    lookup = panel.set_index(["symbol", "open_time"], verify_integrity=True)
    setups_by_symbol: dict[str, pd.DataFrame] = {}
    attempts: list[pd.DataFrame] = []
    pairs: list[pd.DataFrame] = []
    all_setups: list[pd.DataFrame] = []
    for symbol, frame in sorted(frames.items()):
        current_attempts, current_pairs = signal_parent.build_episode_signals(
            frame, symbol, signal_config, params
        )
        setups = signal_parent._setup_rows(current_pairs, frame, start, end, signal_config)
        if len(setups):
            records: list[dict[str, Any]] = []
            for event in setups.to_dict("records"):
                key = (symbol, utc(event["signal_time"]))
                if key not in lookup.index:
                    raise RuntimeError(f"missing context row for {symbol} {event['signal_time']}")
                context = directional_context(
                    lookup.loc[key].to_dict(), int(event["direction"]), config
                )
                records.append(
                    {
                        **event,
                        **context,
                        "source_cohort": (cohort_by_symbol or {}).get(symbol, "parent_52"),
                    }
                )
            setups = pd.DataFrame(records)
            all_setups.append(setups)
        setups_by_symbol[symbol] = setups
        if len(current_attempts):
            attempts.append(current_attempts)
        if len(current_pairs):
            pairs.append(current_pairs)
    return (
        frames,
        setups_by_symbol,
        pd.concat(attempts, ignore_index=True) if attempts else pd.DataFrame(),
        pd.concat(pairs, ignore_index=True) if pairs else pd.DataFrame(),
        pd.concat(all_setups, ignore_index=True) if all_setups else pd.DataFrame(),
        panel,
    )


def context_passes(row: Mapping[str, Any], params: Mapping[str, Any]) -> tuple[bool, str]:
    active = any(float(value) > -0.5 for value in params.values())
    if active and not bool(row.get("context_available", False)):
        return False, "context_unavailable"
    checks = (
        ("breadth_level_min", "context_breadth_level"),
        ("breadth_change5_min", "context_breadth_change5"),
        ("major_score_min", "context_major_score"),
        ("relative_score_min", "context_relative_score"),
        ("context_mean_min", "context_mean"),
    )
    for parameter, column in checks:
        threshold = float(params[parameter])
        if threshold > -0.5 and float(row[column]) < threshold:
            return False, parameter
    return True, "pass"


def filter_setups(
    setups_by_symbol: Mapping[str, pd.DataFrame], params: Mapping[str, Any]
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    filtered: dict[str, pd.DataFrame] = {}
    rejected: list[dict[str, Any]] = []
    for symbol, setups in sorted(setups_by_symbol.items()):
        keep_rows: list[dict[str, Any]] = []
        for row in setups.to_dict("records") if len(setups) else []:
            passed, reason = context_passes(row, params)
            if passed:
                keep_rows.append(row)
            else:
                rejected.append({**row, "context_rejection_reason": reason})
        filtered[symbol] = pd.DataFrame(keep_rows, columns=setups.columns)
    return filtered, pd.DataFrame(rejected)


def evaluate_context(
    frames: Mapping[str, pd.DataFrame],
    setups_by_symbol: Mapping[str, pd.DataFrame],
    signal_config: Mapping[str, Any],
    execution_config: Mapping[str, Any],
    config: Mapping[str, Any],
    context_params: Mapping[str, Any],
    *,
    phase: str,
) -> dict[str, Any]:
    filtered, context_rejections = filter_setups(setups_by_symbol, context_params)
    result = execution_parent.evaluate(
        frames,
        filtered,
        signal_config,
        execution_config,
        config["parents"]["fixed_execution_params"],
        phase=phase,
    )
    result["context_rejections"] = context_rejections
    result["summary"] = {
        **result["summary"],
        "setups_before_context": int(sum(len(value) for value in setups_by_symbol.values())),
        "setups_after_context": int(sum(len(value) for value in filtered.values())),
        "context_rejections": int(len(context_rejections)),
    }
    return result


def _rank_candidate(
    summary: Mapping[str, Any], incumbent: Mapping[str, Any], config: Mapping[str, Any]
) -> tuple[int, float, float, float]:
    incumbent_tail = float(incumbent["p95_raw_net_r"])
    candidate_tail = float(summary["p95_raw_net_r"])
    tail_ok = bool(
        not np.isfinite(incumbent_tail)
        or incumbent_tail <= 0
        or candidate_tail
        >= float(config["selection"]["p95_raw_net_r_retention_min"]) * incumbent_tail
    )
    eligible = bool(summary["eligible"] and tail_ok)
    return (
        1 if eligible else 0,
        float(summary["robust_score_r"]) if eligible else -np.inf,
        float(summary["mean_capped_net_r"]) if eligible else -np.inf,
        float(summary["mean_net_bp"]) if eligible else -np.inf,
    )


def _params_key(params: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(dict(params), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:12]


def _context_for_index(
    panel_lookup: pd.DataFrame,
    symbol: str,
    frame: pd.DataFrame,
    index: int,
    direction: int,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    key = (symbol, utc(frame.loc[index, "open_time"]))
    if key not in panel_lookup.index:
        return {"context_available": False}
    return directional_context(panel_lookup.loc[key].to_dict(), direction, config)


def matched_random_context(
    trades: pd.DataFrame,
    frames: Mapping[str, pd.DataFrame],
    panel: pd.DataFrame,
    signal_config: Mapping[str, Any],
    execution_config: Mapping[str, Any],
    config: Mapping[str, Any],
    context_params: Mapping[str, Any],
    *,
    phase: str,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Match random entries that also satisfy the selected market-context gate."""

    empty = {
        "matched_events": 0,
        "candidate_mean_net_bp": np.nan,
        "control_mean_net_bp": np.nan,
        "excess_bp": np.nan,
        "week_clusters": 0,
        "week_cluster_signflip_p": np.nan,
        "control_reuse_count": 0,
    }
    if trades.empty:
        return pd.DataFrame(), pd.DataFrame(), empty
    lookup = panel.set_index(["symbol", "open_time"], verify_integrity=True)
    split = config["splits"][phase]
    start, end = utc(split["start_inclusive"]), utc(split["end_exclusive"])
    required = int(config["matched_control"]["controls_per_event"])
    radius = int(config["matched_control"]["exclude_radius_bars"])
    seed = str(config["matched_control"]["seed"])
    p_seed = int(config["matched_control"]["p_seed"])
    protected = {
        symbol: group["signal_i"].astype(int).tolist()
        for symbol, group in trades.groupby("symbol", sort=True)
    }
    buckets: dict[str, np.ndarray] = {}
    pools: dict[tuple[str, int, str, int], list[int]] = {}
    for symbol, frame in sorted(frames.items()):
        eligible = signal_parent._eligible_control_indices(frame, start, end, signal_config)
        current_buckets = signal_parent._atr_quintiles(frame, eligible)
        buckets[symbol] = current_buckets
        for direction in (-1, 1):
            for index in np.flatnonzero(eligible & (current_buckets >= 0)):
                if any(abs(int(index) - signal_i) <= radius for signal_i in protected.get(symbol, [])):
                    continue
                context = _context_for_index(lookup, symbol, frame, int(index), direction, config)
                passed, _ = context_passes(context, context_params)
                if not passed:
                    continue
                key = (
                    symbol,
                    direction,
                    signal_parent._halfyear(frame.loc[index, "open_time"]),
                    int(current_buckets[index]),
                )
                pools.setdefault(key, []).append(int(index))
    used: set[tuple[str, int]] = set()
    controls: list[dict[str, Any]] = []
    pairs: list[dict[str, Any]] = []
    fixed_execution = config["parents"]["fixed_execution_params"]
    for event in trades.sort_values(["entry_time", "setup_id"], kind="mergesort").to_dict("records"):
        symbol = str(event["symbol"])
        direction = int(event["direction"])
        frame = frames[symbol]
        bucket = int(buckets[symbol][int(event["signal_i"])])
        key = (
            symbol,
            direction,
            signal_parent._halfyear(utc(event["signal_time"])),
            bucket,
        )
        choices = sorted(
            [index for index in pools.get(key, []) if (symbol, index) not in used],
            key=lambda index: hashlib.sha256(
                f"{seed}|{event['setup_id']}|{symbol}|{index}".encode()
            ).hexdigest(),
        )
        if len(choices) < required:
            pairs.append(
                {
                    "setup_id": event["setup_id"],
                    "symbol": symbol,
                    "entry_time": event["entry_time"],
                    "match_status": "unmatched",
                    "available_controls": len(choices),
                }
            )
            continue
        resolved: list[dict[str, Any]] = []
        for assignment, signal_i in enumerate(choices[:required]):
            used.add((symbol, signal_i))
            entry_i = signal_i + 1
            signal_atr = float(frame.loc[signal_i, "atr"])
            entry = float(frame.loc[entry_i, "open"])
            target_structure = entry - direction * float(event["risk_atr"]) * signal_atr
            control_event = {
                "setup_id": f"context-control-{symbol}-{signal_i}-{direction}",
                "symbol": symbol,
                "direction": direction,
                "signal_i": signal_i,
                "signal_time": frame.loc[signal_i, "open_time"],
                "entry_i": entry_i,
                "entry_time": frame.loc[entry_i, "open_time"],
                "entry_price": entry,
                "signal_atr": signal_atr,
                "k2_low": target_structure
                + float(execution_config["execution"]["stop_buffer_atr"]) * signal_atr,
                "k2_high": target_structure
                - float(execution_config["execution"]["stop_buffer_atr"]) * signal_atr,
                "transition_votes": np.nan,
                "signal_score": np.nan,
            }
            result = execution_parent.resolve_trade(
                frame,
                control_event,
                signal_config,
                execution_config,
                fixed_execution,
                phase_end=end,
            )
            if not result.get("resolved"):
                continue
            resolved.append(result)
            controls.append(
                {
                    "candidate_setup_id": event["setup_id"],
                    "assignment": assignment,
                    "symbol": symbol,
                    "control_signal_i": signal_i,
                    "control_entry_time": result["entry_time"],
                    "direction": direction,
                    "calendar_halfyear": key[2],
                    "atr_quintile": key[3],
                    "copied_risk_atr": float(event["risk_atr"]),
                    "control_net_return": float(result["net_return"]),
                }
            )
        if len(resolved) != required:
            pairs.append(
                {
                    "setup_id": event["setup_id"],
                    "symbol": symbol,
                    "entry_time": event["entry_time"],
                    "match_status": "resolution_failed",
                    "available_controls": len(resolved),
                }
            )
            continue
        control_mean = float(np.mean([row["net_return"] for row in resolved]))
        pairs.append(
            {
                "setup_id": event["setup_id"],
                "symbol": symbol,
                "entry_time": event["entry_time"],
                "match_status": "matched_exact_context_eligible",
                "matched_control_count": required,
                "candidate_net_return": float(event["net_return"]),
                "control_mean_net_return": control_mean,
                "paired_excess_return": float(event["net_return"]) - control_mean,
            }
        )
    controls_frame = pd.DataFrame(controls)
    pairs_frame = pd.DataFrame(pairs)
    matched = pairs_frame[
        pairs_frame.get("match_status", pd.Series(dtype=str)).eq(
            "matched_exact_context_eligible"
        )
    ].copy()
    if matched.empty:
        return controls_frame, pairs_frame, empty
    week = pd.to_datetime(matched["entry_time"], utc=True).dt.strftime("%G-W%V")
    weekly = matched.assign(_week=week).groupby("_week")["paired_excess_return"].mean()
    return controls_frame, pairs_frame, {
        "matched_events": int(len(matched)),
        "candidate_mean_net_bp": float(matched["candidate_net_return"].mean() * 1e4),
        "control_mean_net_bp": float(matched["control_mean_net_return"].mean() * 1e4),
        "excess_bp": float(matched["paired_excess_return"].mean() * 1e4),
        "week_clusters": int(len(weekly)),
        "week_cluster_signflip_p": float(signflip_p(weekly, resamples=100_000, seed=p_seed)),
        "control_reuse_count": 0,
    }


def _write_phase_tables(
    results: Path,
    prefix: str,
    result: Mapping[str, Any],
    *,
    baseline: Mapping[str, Any] | None = None,
) -> None:
    write_csv(result["trades"], results / f"{prefix}_candidate_trades.csv.gz")
    write_csv(result["rejections"], results / f"{prefix}_candidate_position_rejections.csv.gz")
    write_csv(result["context_rejections"], results / f"{prefix}_context_rejections.csv.gz")
    write_csv(result["folds"], results / f"{prefix}_candidate_folds.csv")
    write_csv(result["portfolio_trades"], results / f"{prefix}_portfolio_trades.csv.gz")
    write_csv(result["portfolio_curve"], results / f"{prefix}_portfolio_equity.csv")
    if baseline is not None:
        write_csv(baseline["trades"], results / f"{prefix}_baseline_trades.csv.gz")
        write_csv(baseline["folds"], results / f"{prefix}_baseline_folds.csv")


def development_phase(config_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    experiment = config_path.parent
    prereg = experiment / "preregistration.json"
    manifest_path = experiment / "universe_manifest.json"
    for path in (config_path, prereg, manifest_path, SCRIPT_PATH):
        _assert_head_frozen(path)
    signal_config, raw_execution_config, manifest = _load_contracts(config)
    execution_config = _execution_adapter(config, raw_execution_config)
    end = utc(config["splits"]["development"]["end_exclusive"])
    universe, source_quality, source_summary = signal_parent.load_universe(
        signal_config, end_exclusive=end
    )
    references, reference_quality = load_reference_markets(config, end_exclusive=end)
    frames, setups_by_symbol, attempts, pairs, all_setups, panel = build_phase_inputs(
        universe,
        references,
        signal_config,
        config,
        phase="development",
    )
    baseline_params = deepcopy(config["selection"]["initial"])
    params = deepcopy(baseline_params)
    rows: list[dict[str, Any]] = []
    minimum_gain = float(config["selection"]["minimum_robust_gain_r"])
    for stage, factor in enumerate(config["selection"]["ordered_factors"], start=1):
        incumbent = evaluate_context(
            frames,
            setups_by_symbol,
            signal_config,
            execution_config,
            config,
            params,
            phase="development",
        )
        candidates: list[tuple[Any, dict[str, Any], dict[str, Any]]] = []
        stage_rows: list[dict[str, Any]] = []
        for value in config["selection"]["candidates"][factor]:
            trial = deepcopy(params)
            trial[str(factor)] = value
            evaluated = evaluate_context(
                frames,
                setups_by_symbol,
                signal_config,
                execution_config,
                config,
                trial,
                phase="development",
            )
            candidates.append((value, trial, evaluated))
            stage_rows.append(
                {
                    "stage": stage,
                    "factor": factor,
                    "value": value,
                    "params_key": _params_key(trial),
                    **trial,
                    **evaluated["summary"],
                    **{
                        f"portfolio_{key}": current
                        for key, current in evaluated["portfolio_summary"].items()
                    },
                }
            )
        best_value, best_params, best = max(
            candidates,
            key=lambda item: _rank_candidate(
                item[2]["summary"], incumbent["summary"], config
            ),
        )
        incumbent_rank = _rank_candidate(incumbent["summary"], incumbent["summary"], config)
        best_rank = _rank_candidate(best["summary"], incumbent["summary"], config)
        improvement = best_rank[1] - incumbent_rank[1]
        selected_value = params[factor]
        if best_rank[0] == 1 and (
            incumbent_rank[0] == 0
            or best_value == params[factor]
            or improvement >= minimum_gain
        ):
            params = best_params
            selected_value = best_value
        for row in stage_rows:
            row["robust_gain_vs_stage_incumbent_r"] = (
                float(row["robust_score_r"]) - float(incumbent["summary"]["robust_score_r"])
                if np.isfinite(row["robust_score_r"])
                and np.isfinite(incumbent["summary"]["robust_score_r"])
                else np.nan
            )
            row["stage_selected"] = bool(row["value"] == selected_value)
        rows.extend(stage_rows)
    final = evaluate_context(
        frames,
        setups_by_symbol,
        signal_config,
        execution_config,
        config,
        params,
        phase="development",
    )
    baseline = evaluate_context(
        frames,
        setups_by_symbol,
        signal_config,
        execution_config,
        config,
        baseline_params,
        phase="development",
    )
    changed = bool(params != baseline_params)
    improvement = float(final["summary"]["robust_score_r"]) - float(
        baseline["summary"]["robust_score_r"]
    )
    authorize = bool(
        changed
        and final["summary"]["eligible"]
        and np.isfinite(improvement)
        and improvement >= minimum_gain
    )
    results = experiment / "results"
    results.mkdir(parents=True, exist_ok=True)
    write_csv(source_quality, results / "development_source_quality.csv")
    write_csv(reference_quality, results / "development_reference_quality.csv")
    write_csv(attempts, results / "development_signal_attempts.csv.gz")
    write_csv(pairs, results / "development_signal_pairs.csv.gz")
    write_csv(all_setups, results / "development_context_setups.csv.gz")
    write_csv(panel, results / "development_context_panel.csv.gz")
    write_csv(pd.DataFrame(rows), results / "development_context_grid.csv")
    _write_phase_tables(results, "development", final, baseline=baseline)
    receipt = {
        "experiment_id": config["experiment_id"],
        "phase": "development",
        "frozen": True,
        "status": "frozen_for_confirmation_a"
        if authorize
        else "rejected_before_confirmation_no_robust_context_candidate",
        "confirmation_a_authorized": authorize,
        "selected_context_params": params,
        "baseline_context_params": baseline_params,
        "fixed_signal_params": config["parents"]["fixed_signal_params"],
        "fixed_execution_params": config["parents"]["fixed_execution_params"],
        "source": source_summary,
        "reference_source_rows_read": int(reference_quality["source_rows_read"].sum()),
        "baseline": baseline["summary"],
        "candidate": final["summary"],
        "robust_gain_vs_baseline_r": improvement,
        "portfolio": final["portfolio_summary"],
        "confirmation_a_rows_read": 0,
        "sealed_confirmation_b_rows_read": 0,
        "repository_holdout_rows_read": int(source_summary["repository_holdout_rows_read"]),
        "hashes": {
            "config_sha256": sha256_file(config_path),
            "preregistration_sha256": sha256_file(prereg),
            "universe_manifest_sha256": sha256_file(manifest_path),
            "script_sha256": sha256_file(SCRIPT_PATH),
            "grid_sha256": sha256_file(results / "development_context_grid.csv"),
        },
    }
    write_json(results / "development_receipt.json", receipt)
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))
    return receipt


def confirmation_a_phase(config_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    experiment = config_path.parent
    results = experiment / "results"
    receipt_path = results / "development_receipt.json"
    _assert_head_frozen(receipt_path)
    development = load_config(receipt_path)
    if not bool(development.get("confirmation_a_authorized", False)):
        raise RuntimeError("development did not authorize opening confirmation A")
    for path in (
        config_path,
        experiment / "preregistration.json",
        experiment / "universe_manifest.json",
        SCRIPT_PATH,
    ):
        _assert_head_frozen(path)
    signal_config, raw_execution_config, manifest = _load_contracts(config)
    execution_config = _execution_adapter(config, raw_execution_config)
    end = utc(config["splits"]["confirmation_a"]["end_exclusive"])
    universe, source_quality, source_summary = load_manifest_partition(
        config,
        manifest,
        partition="confirmation_a",
        end_exclusive=end,
    )
    references, reference_quality = load_reference_markets(config, end_exclusive=end)
    cohort_by_symbol = {
        symbol: str(record["cohort"])
        for symbol, record in manifest["confirmation_a"].items()
    }
    frames, setups_by_symbol, attempts, pairs, all_setups, panel = build_phase_inputs(
        universe,
        references,
        signal_config,
        config,
        phase="confirmation_a",
        cohort_by_symbol=cohort_by_symbol,
    )
    params = dict(development["selected_context_params"])
    baseline_params = dict(development["baseline_context_params"])
    candidate = evaluate_context(
        frames,
        setups_by_symbol,
        signal_config,
        execution_config,
        config,
        params,
        phase="confirmation_a",
    )
    baseline = evaluate_context(
        frames,
        setups_by_symbol,
        signal_config,
        execution_config,
        config,
        baseline_params,
        phase="confirmation_a",
    )
    controls, matched_pairs, matched = matched_random_context(
        candidate["trades"],
        frames,
        panel,
        signal_config,
        execution_config,
        config,
        params,
        phase="confirmation_a",
    )
    failure_detail, failure_summary = execution_parent.failure_diagnostics(candidate["trades"])
    symbol_metrics = pd.DataFrame(
        [
            {
                "symbol": symbol,
                "source_cohort": cohort_by_symbol[symbol],
                **execution_parent._base_metrics(
                    group, int(config["matched_control"]["p_seed"])
                ),
            }
            for symbol, group in candidate["trades"].groupby("symbol", sort=True)
        ]
    )
    write_csv(source_quality, results / "confirmation_a_source_quality.csv")
    write_csv(reference_quality, results / "confirmation_a_reference_quality.csv")
    write_csv(attempts, results / "confirmation_a_signal_attempts.csv.gz")
    write_csv(pairs, results / "confirmation_a_signal_pairs.csv.gz")
    write_csv(all_setups, results / "confirmation_a_context_setups.csv.gz")
    write_csv(panel, results / "confirmation_a_context_panel.csv.gz")
    _write_phase_tables(results, "confirmation_a", candidate, baseline=baseline)
    write_csv(controls, results / "confirmation_a_matched_controls.csv.gz")
    write_csv(matched_pairs, results / "confirmation_a_matched_pairs.csv")
    write_csv(failure_detail, results / "confirmation_a_failure_detail.csv.gz")
    write_csv(failure_summary, results / "confirmation_a_failure_modes.csv")
    write_csv(symbol_metrics, results / "confirmation_a_symbol_metrics.csv")
    summary = candidate["summary"]
    gates = config["acceptance_gates"]
    required_positive = int(np.ceil(float(gates["positive_fold_share_min"]) * int(summary["total_folds"])))
    checks = {
        "sample_eligible": bool(summary["eligible"]),
        "mean_net_positive": bool(float(summary["mean_net_bp"]) > 0),
        "capped_mean_net_r_positive": bool(float(summary["mean_capped_net_r"]) > 0),
        "profit_factor_above_one": bool(float(summary["profit_factor"]) > 1),
        "positive_fold_share": bool(int(summary["positive_folds"]) >= required_positive),
        "positive_symbol_share": bool(
            float(summary["positive_symbol_share"]) >= float(gates["positive_symbol_share_min"])
        ),
        "week_cluster_signflip_p": bool(
            float(summary["week_cluster_signflip_p"])
            < float(gates["week_cluster_signflip_p_max"])
        ),
        "matched_excess_positive": bool(float(matched["excess_bp"]) > 0),
        "matched_random_p": bool(
            float(matched["week_cluster_signflip_p"])
            < float(gates["matched_random_p_max"])
        ),
        "portfolio_total_return_positive": bool(
            float(candidate["portfolio_summary"]["total_return"]) > 0
        ),
        "portfolio_drawdown": bool(
            abs(float(candidate["portfolio_summary"]["closed_equity_max_drawdown"]))
            <= float(gates["portfolio_closed_equity_max_drawdown_max"])
        ),
    }
    all_gates = bool(all(checks.values()))
    receipt = {
        "experiment_id": config["experiment_id"],
        "phase": "confirmation_a",
        "frozen": True,
        "status": "passed_confirmation_a_b_still_sealed"
        if all_gates
        else "rejected_confirmation_a_b_remains_sealed",
        "selected_context_params": params,
        "fixed_signal_params": config["parents"]["fixed_signal_params"],
        "fixed_execution_params": config["parents"]["fixed_execution_params"],
        "source": source_summary,
        "reference_source_rows_read": int(reference_quality["source_rows_read"].sum()),
        "baseline": baseline["summary"],
        "candidate": summary,
        "portfolio": candidate["portfolio_summary"],
        "matched_random_context_eligible": matched,
        "gate_checks": checks,
        "all_registered_gates_pass": all_gates,
        "sealed_confirmation_b_rows_read": 0,
        "repository_holdout_rows_read": int(source_summary["repository_holdout_rows_read"]),
        "development_receipt_sha256": sha256_file(receipt_path),
        "production_or_live_changed": False,
    }
    write_json(results / "confirmation_a_receipt.json", receipt)
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--phase", required=True, choices=("development", "confirmation_a"))
    args = parser.parse_args()
    config_path = args.config.resolve()
    config = load_config(config_path)
    if args.phase == "development":
        development_phase(config_path, config)
    else:
        confirmation_a_phase(config_path, config)


if __name__ == "__main__":
    main()
