#!/usr/bin/env python3
"""Screen causal confluence features for the BTCUSDT.P 15m launch ledger.

Feature inputs are limited to the completed signal bar ``t`` and earlier:

* BTC open/high/low/close/volume, ATR14 and HL2 moving averages through ``t``;
* a K1 search over ``t-24`` through ``t-2`` and the intervening K1->K2 path;
* the latest complete 1h bar whose four 15m components are known at ``t`` close;
* ETHUSDT 15m OHLCV through the bar closing at the same decision time; and
* trailing sequence summaries ending at ``t``.

The fixed parent ledger supplies the next-open entry and future runner outcome.
Only ``net_return`` and other outcome columns may read after ``t``. Selection
uses expanding-time 2023 folds with a 96-bar label purge, commits one nominated
variant, then replays that contract on 2024. The 2025-through-2026-02 phase is a
separate already-seen diagnostic audit. Repository holdout begins 2026-05-04
and is never materialized by this script.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from scripts.backtest_two_key_candle_pine_v8_btc_1h import signflip_p
from scripts.research_btcusdtp_15m_dual_ma_runner import matched_controls
from scripts.research_btcusdtp_15m_high_recall_l2_runner import (
    corrected_failure_mechanics,
    load_base_until,
    load_config as load_parent_config,
    load_v2_config,
    score_permutation_p,
)
from scripts.research_btcusdtp_15m_ma_state_trend import (
    json_value,
    metrics,
    utc,
    write_csv,
    write_json,
)
from scripts.research_two_key_candle_ma_retest_1h import pine_rma, sha256_file


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-btcusdtp-15m-multifactor-confluence-preholdout-20260904-v1"
EXPERIMENT = ROOT / "experiments" / "active" / EXPERIMENT_ID
CONFIG_PATH = EXPERIMENT / "config.json"
RESULTS = EXPERIMENT / "results"
MODELS = RESULTS / "models"
SELECTION_RECEIPT = RESULTS / "selection_receipt.json"
MODEL_CONTRACT = RESULTS / "model_contract.json"
CONFIRMATION_RECEIPT = RESULTS / "confirmation_receipt.json"
AUDIT_RECEIPT = RESULTS / "audit_receipt.json"
SCRIPT_PATH = Path(__file__).resolve()
BAR_DELTA = pd.Timedelta(minutes=15)
FORBIDDEN_FEATURE_TOKENS = (
    "net_return",
    "gross_return",
    "outcome",
    "exit_",
    "hold_bars",
    "runner_armed",
    "runner_arm",
    "mfe",
    "mae",
    "capture",
    "gave_back",
    "return_r",
)


def load_config() -> dict[str, Any]:
    """Load the committed experiment contract."""

    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _json_hash(value: object) -> str:
    payload = json.dumps(json_value(value), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_div(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator.astype(float).div(denominator.astype(float).replace(0.0, np.nan))


def _rolling_rank_last(values: pd.Series, window: int, minimum: int) -> pd.Series:
    """Return the causal percentile rank of each current value in its trailing window."""

    return values.rolling(window, min_periods=minimum).rank(pct=True)


def _rsi(values: pd.Series, length: int = 14) -> np.ndarray:
    """Pine-RMA RSI from closes through the current row."""

    delta = values.astype(float).diff().to_numpy(dtype=float)
    gains = np.where(delta > 0.0, delta, 0.0)
    losses = np.where(delta < 0.0, -delta, 0.0)
    avg_gain = pine_rma(gains, length)
    avg_loss = pine_rma(losses, length)
    rs = avg_gain / np.maximum(avg_loss, 1e-12)
    return 100.0 - 100.0 / (1.0 + rs)


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> np.ndarray:
    """Pine-RMA ADX from high/low/close through the current row."""

    prior_close = close.shift(1)
    true_range = np.maximum(
        high - low,
        np.maximum((high - prior_close).abs(), (low - prior_close).abs()),
    )
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0.0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0.0), down, 0.0)
    atr = pine_rma(true_range.to_numpy(dtype=float), length)
    plus = 100.0 * pine_rma(np.asarray(plus_dm, dtype=float), length) / atr
    minus = 100.0 * pine_rma(np.asarray(minus_dm, dtype=float), length) / atr
    dx = 100.0 * np.abs(plus - minus) / np.maximum(plus + minus, 1e-12)
    return pine_rma(dx, length)


def _run_length(mask: pd.Series) -> pd.Series:
    """Causal current run length of a boolean state."""

    mask = mask.fillna(False).astype(bool)
    groups = mask.ne(mask.shift(fill_value=False)).cumsum()
    run = mask.groupby(groups).cumcount().add(1)
    return run.where(mask, 0).astype(float)


def _market_features_one_segment(segment: pd.DataFrame) -> pd.DataFrame:
    """Build BTC trailing features without crossing a source gap.

    Reads OHLCV and ATR only at each row and earlier. Windows are EMA/SMA
    12/20/26/30/60/120/160/240, returns 4/8/12/24/32/48/96, volatility
    12/20/24/96/384, volume 4/12/20/48/96, and sequence summaries over 32 bars.
    """

    out = segment.copy()
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    close = out["close"].astype(float)
    open_ = out["open"].astype(float)
    volume = out["volume"].astype(float)
    atr = out["atr"].astype(float).replace(0.0, np.nan)
    hl2 = (high + low) / 2.0
    for length in (20, 30, 60, 120, 160, 240):
        out[f"sma{length}"] = hl2.rolling(length, min_periods=length).mean()
    for length in (12, 20, 26, 30):
        out[f"ema{length}"] = hl2.ewm(span=length, adjust=False, min_periods=length).mean()

    out["ema30_slope4_atr"] = _safe_div(out["ema30"] - out["ema30"].shift(4), atr * 4.0)
    out["ema30_accel4_atr"] = _safe_div(
        (out["ema30"] - out["ema30"].shift(4))
        - (out["ema30"].shift(4) - out["ema30"].shift(8)),
        atr * 4.0,
    )
    out["sma60_slope4_atr"] = _safe_div(out["sma60"] - out["sma60"].shift(4), atr * 4.0)
    out["sma60_accel4_atr"] = _safe_div(
        (out["sma60"] - out["sma60"].shift(4))
        - (out["sma60"].shift(4) - out["sma60"].shift(8)),
        atr * 4.0,
    )
    spread = out["ema30"] - out["sma60"]
    out["ema30_sma60_spread_delta4_atr"] = _safe_div(spread - spread.shift(4), atr)
    order_pairs = [
        out["ema20"] - out["ema30"],
        out["ema30"] - out["sma60"],
        out["sma60"] - out["sma120"],
        out["sma120"] - out["sma160"],
        out["sma160"] - out["sma240"],
    ]
    out["ma_order_score"] = np.mean(np.vstack([np.sign(v) for v in order_pairs]), axis=0)
    bundle = pd.concat(
        [out[f"sma{x}"] for x in (20, 30, 60, 120, 160, 240)], axis=1
    )
    out["ma_bundle_width_atr_raw"] = _safe_div(bundle.max(axis=1) - bundle.min(axis=1), atr)
    out["ema30_positive_age"] = _run_length(close.gt(out["ema30"]))
    out["ema30_negative_age"] = _run_length(close.lt(out["ema30"]))
    out["ema30_sma60_positive_age"] = _run_length(out["ema30"].gt(out["sma60"]))
    out["ema30_sma60_negative_age"] = _run_length(out["ema30"].lt(out["sma60"]))

    log_return = np.log(close).diff()
    out["log_return"] = log_return
    out["return4"] = np.log(close / close.shift(4))
    out["return8"] = np.log(close / close.shift(8))
    out["return16"] = np.log(close / close.shift(16))
    out["return24"] = np.log(close / close.shift(24))
    out["return48"] = np.log(close / close.shift(48))
    out["return96"] = np.log(close / close.shift(96))
    rv12 = np.sqrt(log_return.pow(2).rolling(12, min_periods=12).sum())
    out["rv12_ratio96_raw"] = _safe_div(
        rv12, rv12.shift(1).rolling(96, min_periods=48).median()
    )
    out["atr_percentile384_raw"] = _rolling_rank_last(atr, 384, 192)
    out["atr_change24_raw"] = _safe_div(atr, atr.shift(24)) - 1.0
    candle_range = high - low
    out["range_percentile96_raw"] = _rolling_rank_last(candle_range, 96, 48)
    basis = close.rolling(20, min_periods=20).mean()
    stdev = close.rolling(20, min_periods=20).std(ddof=0)
    bb_width = 4.0 * stdev
    out["bb_width_atr_raw"] = _safe_div(bb_width, atr)
    out["bb_width_ratio96_raw"] = _safe_div(
        bb_width, bb_width.shift(1).rolling(96, min_periods=48).median()
    )
    out["bb_release24_raw"] = _safe_div(
        bb_width, bb_width.shift(1).rolling(24, min_periods=12).min()
    )

    out["volume_ratio20_raw"] = _safe_div(
        volume, volume.shift(1).rolling(20, min_periods=12).median()
    )
    out["volume_ratio96_raw"] = _safe_div(
        volume, volume.shift(1).rolling(96, min_periods=48).median()
    )
    out["volume_impulse4_raw"] = _safe_div(
        volume.rolling(4, min_periods=4).mean(),
        volume.shift(4).rolling(20, min_periods=12).median(),
    )
    signed_volume = np.sign(close.diff()).fillna(0.0) * volume
    out["volume_balance12_raw"] = _safe_div(
        signed_volume.rolling(12, min_periods=12).sum(),
        volume.rolling(12, min_periods=12).sum(),
    )
    out["volume_balance48_raw"] = _safe_div(
        signed_volume.rolling(48, min_periods=32).sum(),
        volume.rolling(48, min_periods=32).sum(),
    )
    obv = signed_volume.cumsum()
    out["obv_slope12_norm_raw"] = _safe_div(
        obv - obv.shift(12), volume.rolling(12, min_periods=12).sum()
    )
    out["price_volume_corr24_raw"] = log_return.rolling(24, min_periods=16).corr(
        np.log(volume.replace(0.0, np.nan)).diff()
    )

    out["rsi14_raw"] = _rsi(close, 14)
    macd = out["ema12"] - out["ema26"]
    signal = macd.ewm(span=9, adjust=False, min_periods=9).mean()
    out["macd_hist_atr_raw"] = _safe_div(macd - signal, atr)
    prior_high20 = high.shift(1).rolling(20, min_periods=20).max()
    prior_low20 = low.shift(1).rolling(20, min_periods=20).min()
    out["donchian20_long"] = _safe_div(close - prior_low20, prior_high20 - prior_low20)
    out["donchian20_short"] = _safe_div(prior_high20 - close, prior_high20 - prior_low20)
    prior_high8 = high.shift(1).rolling(8, min_periods=8).max()
    prior_low8 = low.shift(1).rolling(8, min_periods=8).min()
    out["pullback_long8_atr"] = _safe_div(prior_high8 - close, atr)
    out["pullback_short8_atr"] = _safe_div(close - prior_low8, atr)
    prior_high24 = high.shift(1).rolling(24, min_periods=24).max()
    prior_low24 = low.shift(1).rolling(24, min_periods=24).min()
    out["breakout_long24_atr"] = _safe_div(close - prior_high24, atr)
    out["breakout_short24_atr"] = _safe_div(prior_low24 - close, atr)
    out["return_accel_atr_raw"] = _safe_div(
        (close - close.shift(4)) - (close.shift(4) - close.shift(8)), atr
    )
    native_up = close.gt(open_)
    native_down = close.lt(open_)
    out["up_candle_streak"] = _run_length(native_up)
    out["down_candle_streak"] = _run_length(native_down)

    ret = log_return
    out["ret_autocorr1_32_raw"] = ret.rolling(32, min_periods=24).corr(ret.shift(1))
    out["ret_autocorr4_32_raw"] = ret.rolling(32, min_periods=24).corr(ret.shift(4))
    up_share = ret.gt(0.0).rolling(32, min_periods=24).mean().clip(1e-6, 1.0 - 1e-6)
    out["return_sign_entropy32_raw"] = -(
        up_share * np.log2(up_share) + (1.0 - up_share) * np.log2(1.0 - up_share)
    )
    signs = np.sign(ret)
    out["return_sign_change32_raw"] = signs.ne(signs.shift(1)).rolling(
        32, min_periods=24
    ).mean()
    out["return_skew32_raw"] = ret.rolling(32, min_periods=24).skew()
    out["return_kurt32_raw"] = ret.rolling(32, min_periods=24).kurt()
    high32 = high.rolling(32, min_periods=32).max()
    low32 = low.rolling(32, min_periods=32).min()
    out["close_range_long32"] = _safe_div(close - low32, high32 - low32)
    out["close_range_short32"] = _safe_div(high32 - close, high32 - low32)
    out["drawdown_long32_atr"] = _safe_div(close - high32, atr)
    out["drawdown_short32_atr"] = _safe_div(low32 - close, atr)
    out["path_efficiency32_raw"] = _safe_div(
        (close - close.shift(32)).abs(), close.diff().abs().rolling(32, min_periods=32).sum()
    )
    return out.replace([np.inf, -np.inf], np.nan)


def add_market_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Apply trailing BTC features independently inside each contiguous segment."""

    parts = [
        _market_features_one_segment(part.copy())
        for _, part in frame.groupby("segment_id", sort=True)
    ]
    if not parts:
        return frame.copy()
    return pd.concat(parts).sort_index()


def _aggregate_complete_1h(frame: pd.DataFrame) -> pd.DataFrame:
    """Aggregate only exact four-bar BTC hours and expose them at hour close."""

    rows: list[pd.DataFrame] = []
    for _, part in frame.groupby("segment_id", sort=True):
        indexed = part.set_index("open_time").sort_index()
        agg = indexed.resample("1h", label="left", closed="left").agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
            bars=("close", "count"),
        )
        agg = agg.loc[agg["bars"].eq(4)].copy()
        if agg.empty:
            continue
        expected = pd.Series(agg.index, index=agg.index).add(pd.Timedelta(hours=1))
        agg["available_time"] = expected.to_numpy()
        rows.append(agg.reset_index().rename(columns={"open_time": "hour_open"}))
    if not rows:
        return pd.DataFrame()
    hourly = pd.concat(rows, ignore_index=True).sort_values("hour_open")
    high = hourly["high"].astype(float)
    low = hourly["low"].astype(float)
    close = hourly["close"].astype(float)
    prior_close = close.shift(1)
    tr = np.maximum(
        high - low,
        np.maximum((high - prior_close).abs(), (low - prior_close).abs()),
    )
    hourly["atr14"] = pine_rma(tr.to_numpy(dtype=float), 14)
    hl2 = (high + low) / 2.0
    hourly["ema30"] = hl2.ewm(span=30, adjust=False, min_periods=30).mean()
    hourly["sma60"] = hl2.rolling(60, min_periods=60).mean()
    hourly["rsi14"] = _rsi(close, 14)
    hourly["adx14"] = _adx(high, low, close, 14)
    hourly["sma60_slope4"] = hourly["sma60"] - hourly["sma60"].shift(4)
    hourly["return6"] = close - close.shift(6)
    return hourly.replace([np.inf, -np.inf], np.nan)


def load_eth_until(config: Mapping[str, Any], end_exclusive: pd.Timestamp) -> pd.DataFrame:
    """Load the pre-holdout ETH file and stop materializing rows at the phase end."""

    source = config["sources"]["eth_15m"]
    path = ROOT / str(source["path"])
    audit_path = ROOT / str(source["audit"])
    if sha256_file(path) != str(source["sha256"]):
        raise RuntimeError("ETH source hash drift")
    if sha256_file(audit_path) != str(source["audit_sha256"]):
        raise RuntimeError("ETH audit hash drift")
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if int(audit.get("holdout_ohlcv_rows_materialized", -1)) != 0:
        raise RuntimeError("ETH source audit is not pre-holdout safe")
    pieces: list[pd.DataFrame] = []
    end = utc(end_exclusive)
    for chunk in pd.read_csv(path, chunksize=50_000):
        chunk["open_time"] = pd.to_datetime(chunk["open_time"], utc=True)
        before = chunk.loc[chunk["open_time"].lt(end)].copy()
        if len(before):
            pieces.append(before)
        if chunk["open_time"].ge(end).any():
            break
    if not pieces:
        raise RuntimeError("no ETH rows before requested phase end")
    frame = pd.concat(pieces, ignore_index=True).sort_values("open_time")
    if utc(frame["open_time"].max()) >= end:
        raise RuntimeError("ETH phase loader crossed end boundary")
    gaps = frame["open_time"].diff().ne(BAR_DELTA)
    gaps.iloc[0] = True
    frame["segment_id"] = gaps.cumsum().astype(int)
    return frame.reset_index(drop=True)


def _eth_features_one_segment(segment: pd.DataFrame) -> pd.DataFrame:
    """Build causal ETH market-regime features through each 15m close."""

    out = segment.copy()
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    close = out["close"].astype(float)
    hl2 = (high + low) / 2.0
    prior_close = close.shift(1)
    tr = np.maximum(
        high - low,
        np.maximum((high - prior_close).abs(), (low - prior_close).abs()),
    )
    out["atr14"] = pine_rma(tr.to_numpy(dtype=float), 14)
    out["ema30"] = hl2.ewm(span=30, adjust=False, min_periods=30).mean()
    out["sma60"] = hl2.rolling(60, min_periods=60).mean()
    out["return4"] = np.log(close / close.shift(4))
    out["return16"] = np.log(close / close.shift(16))
    out["return96"] = np.log(close / close.shift(96))
    out["log_return"] = np.log(close).diff()
    out["rsi14"] = _rsi(close, 14)
    out["adx14"] = _adx(high, low, close, 14)
    out["available_time"] = out["open_time"] + BAR_DELTA
    return out.replace([np.inf, -np.inf], np.nan)


def add_eth_features(frame: pd.DataFrame) -> pd.DataFrame:
    parts = [
        _eth_features_one_segment(part.copy())
        for _, part in frame.groupby("segment_id", sort=True)
    ]
    return pd.concat(parts, ignore_index=True).sort_values("available_time")


def load_events(config: Mapping[str, Any], phase: str) -> pd.DataFrame:
    """Load exactly one parent outcome ledger and enforce its registered time box."""

    mapping = {
        "development": (
            "development_start_inclusive",
            "development_end_exclusive",
        ),
        "confirmation": (
            "confirmation_start_inclusive",
            "confirmation_end_exclusive",
        ),
        "audit": (
            "diagnostic_audit_start_inclusive",
            "diagnostic_audit_end_exclusive",
        ),
    }
    if phase not in mapping:
        raise ValueError(f"unknown event phase: {phase}")
    source = config["sources"]["event_ledgers"][phase]
    path = ROOT / str(source["path"])
    if sha256_file(path) != str(source["sha256"]):
        raise RuntimeError(f"{phase} parent event ledger hash drift")
    events = pd.read_csv(path)
    events["signal_time"] = pd.to_datetime(events["signal_time"], utc=True)
    events["entry_time"] = pd.to_datetime(events["entry_time"], utc=True)
    start = utc(config["splits"][mapping[phase][0]])
    end = utc(config["splits"][mapping[phase][1]])
    if events.empty or events["entry_time"].lt(start).any() or events["entry_time"].ge(end).any():
        raise RuntimeError(f"{phase} ledger escapes registered time box")
    holdout = utc(config["sources"]["holdout_start"])
    if events["signal_time"].ge(holdout).any():
        raise RuntimeError("repository holdout event materialized")
    return events.sort_values(["entry_time", "setup_id"], kind="mergesort").reset_index(drop=True)


def _assert_event_alignment(events: pd.DataFrame, frame: pd.DataFrame) -> None:
    indices = events["signal_i"].astype(int).to_numpy()
    if indices.max(initial=-1) >= len(frame):
        raise RuntimeError("event signal index outside phase frame")
    actual_signal = pd.to_datetime(frame.loc[indices, "open_time"].to_numpy(), utc=True)
    expected_signal = pd.to_datetime(events["signal_time"].to_numpy(), utc=True)
    if not np.array_equal(actual_signal.view("i8"), expected_signal.view("i8")):
        raise RuntimeError("event signal index/time mismatch")
    entry_indices = indices + 1
    actual_entry = pd.to_datetime(frame.loc[entry_indices, "open_time"].to_numpy(), utc=True)
    expected_entry = pd.to_datetime(events["entry_time"].to_numpy(), utc=True)
    if not np.array_equal(actual_entry.view("i8"), expected_entry.view("i8")):
        raise RuntimeError("event next-open index/time mismatch")


def _sample_directional(
    output: pd.DataFrame,
    market: pd.DataFrame,
    indices: np.ndarray,
    direction: np.ndarray,
) -> None:
    """Attach trend, volatility, participation, momentum and sequence fields."""

    def take(name: str) -> np.ndarray:
        return market[name].to_numpy(dtype=float)[indices]

    atr = take("atr")
    output["ema30_side_age"] = np.where(
        direction > 0, take("ema30_positive_age"), take("ema30_negative_age")
    )
    output["ema30_sma60_alignment_age"] = np.where(
        direction > 0,
        take("ema30_sma60_positive_age"),
        take("ema30_sma60_negative_age"),
    )
    for target, source in (
        ("ema30_slope4_dir_atr", "ema30_slope4_atr"),
        ("ema30_accel4_dir_atr", "ema30_accel4_atr"),
        ("sma60_slope4_dir_atr", "sma60_slope4_atr"),
        ("sma60_accel4_dir_atr", "sma60_accel4_atr"),
        ("ema30_sma60_spread_delta4_dir_atr", "ema30_sma60_spread_delta4_atr"),
    ):
        output[target] = direction * take(source)
    output["ma_order_score_dir"] = direction * take("ma_order_score")
    output["ma_bundle_width_atr"] = take("ma_bundle_width_atr_raw")
    output["price_extension_sma60_atr"] = direction * (
        take("close") - take("sma60")
    ) / atr

    for target, source in (
        ("atr_percentile384", "atr_percentile384_raw"),
        ("atr_change24", "atr_change24_raw"),
        ("rv12_ratio96", "rv12_ratio96_raw"),
        ("bb_width_atr", "bb_width_atr_raw"),
        ("bb_width_ratio96", "bb_width_ratio96_raw"),
        ("bb_release24", "bb_release24_raw"),
        ("range_percentile96", "range_percentile96_raw"),
        ("volume_ratio96", "volume_ratio96_raw"),
        ("volume_impulse4", "volume_impulse4_raw"),
        ("price_volume_corr24", "price_volume_corr24_raw"),
        ("ret_autocorr1_32", "ret_autocorr1_32_raw"),
        ("ret_autocorr4_32", "ret_autocorr4_32_raw"),
        ("return_sign_entropy32", "return_sign_entropy32_raw"),
        ("return_sign_change32", "return_sign_change32_raw"),
        ("return_kurt32", "return_kurt32_raw"),
        ("path_efficiency32", "path_efficiency32_raw"),
    ):
        output[target] = take(source)
    output["signed_volume_balance12"] = direction * take("volume_balance12_raw")
    output["signed_volume_balance48"] = direction * take("volume_balance48_raw")
    output["obv_slope12_norm"] = direction * take("obv_slope12_norm_raw")
    output["rsi14_dir"] = direction * (take("rsi14_raw") - 50.0)
    output["macd_hist_dir_atr"] = direction * take("macd_hist_atr_raw")
    output["donchian20_position_dir"] = np.where(
        direction > 0, take("donchian20_long"), take("donchian20_short")
    )
    candle_range = np.maximum(take("high") - take("low"), 1e-12)
    output["close_location_dir"] = np.where(
        direction > 0,
        (take("close") - take("low")) / candle_range,
        (take("high") - take("close")) / candle_range,
    )
    output["pullback_from_extreme8_atr"] = np.where(
        direction > 0, take("pullback_long8_atr"), take("pullback_short8_atr")
    )
    output["breakout_prior24_atr"] = np.where(
        direction > 0, take("breakout_long24_atr"), take("breakout_short24_atr")
    )
    output["return_accel_dir_atr"] = direction * take("return_accel_atr_raw")
    output["candle_streak_dir"] = np.where(
        direction > 0, take("up_candle_streak"), take("down_candle_streak")
    )
    output["return_skew32_dir"] = direction * take("return_skew32_raw")
    output["close_range_position32_dir"] = np.where(
        direction > 0, take("close_range_long32"), take("close_range_short32")
    )
    output["drawdown_from_high32_dir"] = np.where(
        direction > 0, take("drawdown_long32_atr"), take("drawdown_short32_atr")
    )


def _attach_k1k2(
    output: pd.DataFrame,
    market: pd.DataFrame,
    config: Mapping[str, Any],
) -> None:
    """Attach most-recent causal K1 and K1->K2 relationship features.

    K1 may use only bars ``K2-24`` through ``K2-2``. It must open at or behind
    EMA30 by at most 0.1 ATR, close at least 0.15 ATR through EMA30, have a
    directional body of at least 0.35 ATR, and range of at least 0.7 ATR.
    Intervening path statistics end at K2. No K2+1 value is used.
    """

    spec = config["feature_contract"]["k1_search"]
    min_gap = int(spec["min_gap_bars"])
    max_gap = int(spec["max_gap_bars"])
    opens = market["open"].to_numpy(dtype=float)
    highs = market["high"].to_numpy(dtype=float)
    lows = market["low"].to_numpy(dtype=float)
    closes = market["close"].to_numpy(dtype=float)
    volumes = market["volume"].to_numpy(dtype=float)
    atrs = market["atr"].to_numpy(dtype=float)
    ma = market["ema30"].to_numpy(dtype=float)
    vol_ratio = market["volume_ratio20_raw"].to_numpy(dtype=float)
    segment = market["segment_id"].to_numpy(dtype=int)
    records: list[dict[str, float]] = []
    for event in output[["signal_i", "direction"]].to_dict("records"):
        k2 = int(event["signal_i"])
        side = int(event["direction"])
        atr2 = float(atrs[k2])
        base = {
            "k1_found": 0.0,
            "k1_gap_bars": np.nan,
            "k1_body_atr": np.nan,
            "k1_range_atr": np.nan,
            "k1_close_location": np.nan,
            "k1_volume_ratio20": np.nan,
            "k1_to_k2_move_atr": np.nan,
            "k1_to_k2_peak_atr": np.nan,
            "k1_to_k2_retrace_atr": np.nan,
            "between_wrong_side_share": np.nan,
            "between_ma_side_continuity": np.nan,
            "k2_touch_depth_atr": (
                (ma[k2] - lows[k2]) / atr2
                if side > 0
                else (highs[k2] - ma[k2]) / atr2
            ),
            "k2_body_clearance_atr": (
                (min(opens[k2], closes[k2]) - ma[k2]) / atr2
                if side > 0
                else (ma[k2] - max(opens[k2], closes[k2])) / atr2
            ),
            "k2_rejection_wick_share": (
                (min(opens[k2], closes[k2]) - lows[k2])
                / max(highs[k2] - lows[k2], 1e-12)
                if side > 0
                else (highs[k2] - max(opens[k2], closes[k2]))
                / max(highs[k2] - lows[k2], 1e-12)
            ),
            "k2_to_k1_volume_ratio": np.nan,
        }
        if not np.isfinite(atr2) or atr2 <= 0.0 or not np.isfinite(ma[k2]):
            records.append(base)
            continue
        found: int | None = None
        for k1 in range(k2 - min_gap, max(-1, k2 - max_gap - 1), -1):
            if k1 < 0 or segment[k1] != segment[k2]:
                continue
            atr1 = float(atrs[k1])
            if not np.isfinite(atr1) or atr1 <= 0.0 or not np.isfinite(ma[k1]):
                continue
            open_side = side * (opens[k1] - ma[k1]) / atr1
            close_side = side * (closes[k1] - ma[k1]) / atr1
            body = side * (closes[k1] - opens[k1]) / atr1
            range_atr = (highs[k1] - lows[k1]) / atr1
            if (
                open_side <= float(spec["open_side_max_atr"])
                and close_side >= float(spec["close_side_min_atr"])
                and body >= float(spec["body_min_atr"])
                and range_atr >= float(spec["range_min_atr"])
            ):
                found = k1
                break
        if found is None:
            records.append(base)
            continue
        k1 = found
        gap = k2 - k1
        path_slice = slice(k1, k2 + 1)
        if side > 0:
            peak = float(np.nanmax(highs[path_slice]))
            close_location = (closes[k1] - lows[k1]) / max(highs[k1] - lows[k1], 1e-12)
            peak_atr = (peak - closes[k1]) / atr2
        else:
            peak = float(np.nanmin(lows[path_slice]))
            close_location = (highs[k1] - closes[k1]) / max(highs[k1] - lows[k1], 1e-12)
            peak_atr = (closes[k1] - peak) / atr2
        move = side * (closes[k2] - closes[k1]) / atr2
        between = np.arange(k1 + 1, k2, dtype=int)
        if len(between):
            signed_sides = side * (closes[between] - ma[between])
            wrong_share = float(np.mean(signed_sides < 0.0))
            continuity = float(np.mean(signed_sides >= 0.0))
        else:
            wrong_share = 0.0
            continuity = 1.0
        base.update(
            {
                "k1_found": 1.0,
                "k1_gap_bars": float(gap),
                "k1_body_atr": side * (closes[k1] - opens[k1]) / atrs[k1],
                "k1_range_atr": (highs[k1] - lows[k1]) / atrs[k1],
                "k1_close_location": close_location,
                "k1_volume_ratio20": vol_ratio[k1],
                "k1_to_k2_move_atr": move,
                "k1_to_k2_peak_atr": peak_atr,
                "k1_to_k2_retrace_atr": peak_atr - move,
                "between_wrong_side_share": wrong_share,
                "between_ma_side_continuity": continuity,
                "k2_to_k1_volume_ratio": volumes[k2] / max(volumes[k1], 1e-12),
            }
        )
        records.append(base)
    attached = pd.DataFrame(records, index=output.index)
    for column in attached.columns:
        output[column] = attached[column]


def _attach_higher_timeframe(output: pd.DataFrame, market: pd.DataFrame) -> None:
    """Attach latest fully completed BTC 1h context at the 15m signal close."""

    hourly = _aggregate_complete_1h(market)
    if hourly.empty:
        raise RuntimeError("no complete 1h bars")
    left = output[["signal_time", "direction"]].copy()
    left["decision_time"] = pd.to_datetime(left["signal_time"], utc=True) + BAR_DELTA
    left["_row"] = np.arange(len(left))
    merged = pd.merge_asof(
        left.sort_values("decision_time"),
        hourly.sort_values("available_time"),
        left_on="decision_time",
        right_on="available_time",
        direction="backward",
        tolerance=pd.Timedelta(minutes=60),
    ).sort_values("_row")
    side = merged["direction"].to_numpy(dtype=float)
    atr = merged["atr14"].to_numpy(dtype=float)
    output["htf_age_minutes"] = (
        merged["decision_time"] - merged["available_time"]
    ).dt.total_seconds().to_numpy(dtype=float) / 60.0
    output["htf_close_ema30_dir_atr"] = side * (
        merged["close"].to_numpy(dtype=float) - merged["ema30"].to_numpy(dtype=float)
    ) / atr
    output["htf_ema30_sma60_spread_dir_atr"] = side * (
        merged["ema30"].to_numpy(dtype=float) - merged["sma60"].to_numpy(dtype=float)
    ) / atr
    output["htf_sma60_slope4_dir_atr"] = side * merged[
        "sma60_slope4"
    ].to_numpy(dtype=float) / (atr * 4.0)
    output["htf_return6_dir_atr"] = side * merged["return6"].to_numpy(dtype=float) / atr
    output["htf_rsi14_dir"] = side * (merged["rsi14"].to_numpy(dtype=float) - 50.0)
    output["htf_adx14"] = merged["adx14"].to_numpy(dtype=float)
    output["htf_ma_alignment"] = (
        side * (merged["ema30"].to_numpy(dtype=float) - merged["sma60"].to_numpy(dtype=float))
        > 0.0
    ).astype(float)
    valid_age = output["htf_age_minutes"].between(0.0, 45.0) | output[
        "htf_age_minutes"
    ].isna()
    if not bool(valid_age.all()):
        raise RuntimeError("partial or stale 1h bar leaked into feature matrix")


def _attach_eth(
    output: pd.DataFrame,
    market: pd.DataFrame,
    eth: pd.DataFrame,
) -> None:
    """Attach ETH regime known at the same completed 15m decision time."""

    left = output[["signal_time", "direction"]].copy()
    left["decision_time"] = pd.to_datetime(left["signal_time"], utc=True) + BAR_DELTA
    left["_row"] = np.arange(len(left))
    columns = [
        "available_time",
        "close",
        "atr14",
        "ema30",
        "sma60",
        "return4",
        "return16",
        "return96",
        "rsi14",
        "adx14",
    ]
    merged = pd.merge_asof(
        left.sort_values("decision_time"),
        eth[columns].sort_values("available_time"),
        left_on="decision_time",
        right_on="available_time",
        direction="backward",
        tolerance=BAR_DELTA,
    ).sort_values("_row")
    side = merged["direction"].to_numpy(dtype=float)
    atr = merged["atr14"].to_numpy(dtype=float)
    output["eth_return4_dir"] = side * merged["return4"].to_numpy(dtype=float)
    output["eth_return16_dir"] = side * merged["return16"].to_numpy(dtype=float)
    output["eth_return96_dir"] = side * merged["return96"].to_numpy(dtype=float)
    output["eth_close_ema30_dir_atr"] = side * (
        merged["close"].to_numpy(dtype=float) - merged["ema30"].to_numpy(dtype=float)
    ) / atr
    output["eth_ema30_sma60_spread_dir_atr"] = side * (
        merged["ema30"].to_numpy(dtype=float) - merged["sma60"].to_numpy(dtype=float)
    ) / atr
    output["eth_rsi14_dir"] = side * (merged["rsi14"].to_numpy(dtype=float) - 50.0)
    output["eth_adx14"] = merged["adx14"].to_numpy(dtype=float)

    eth_returns = eth.set_index("open_time")["log_return"].reindex(market["open_time"])
    btc_returns = pd.Series(market["log_return"].to_numpy(dtype=float), index=market["open_time"])
    corr = btc_returns.rolling(96, min_periods=64).corr(eth_returns)
    corr_map = corr.to_dict()
    signal_times = pd.to_datetime(output["signal_time"], utc=True)
    output["btc_eth_return_corr96"] = signal_times.map(corr_map).to_numpy(dtype=float)
    indices = output["signal_i"].astype(int).to_numpy()
    btc_return16 = market["return16"].to_numpy(dtype=float)[indices]
    output["eth_btc_relative16_dir"] = output["eth_return16_dir"].to_numpy(dtype=float) - side * btc_return16


def _attach_calendar_and_vote(output: pd.DataFrame) -> None:
    """Attach known calendar encodings and a fixed Pine-compatible confluence vote."""

    times = pd.to_datetime(output["signal_time"], utc=True)
    hour = times.dt.hour.to_numpy(dtype=float) + times.dt.minute.to_numpy(dtype=float) / 60.0
    output["utc_hour_sin"] = np.sin(2.0 * np.pi * hour / 24.0)
    output["utc_hour_cos"] = np.cos(2.0 * np.pi * hour / 24.0)
    output["weekend"] = times.dt.weekday.ge(5).astype(float)
    flags = pd.DataFrame(
        {
            "k1_clean": output["k1_found"].eq(1.0)
            & output["k1_gap_bars"].between(2.0, 12.0)
            & output["between_wrong_side_share"].le(0.0),
            "trend_constructive": output["ema30_side_age"].between(1.0, 24.0)
            & output["ma_order_score_dir"].ge(0.2)
            & output["sma60_slope4_dir_atr"].ge(0.0),
            "htf_aligned": output["htf_ma_alignment"].eq(1.0)
            & output["htf_close_ema30_dir_atr"].ge(0.0),
            "volatility_constructive": output["atr_percentile384"].between(0.15, 0.85)
            & output["bb_release24"].between(0.8, 2.5),
            "participation_confirmed": output["volume_ratio96"].ge(1.0)
            | output["signed_volume_balance12"].gt(0.0),
            "momentum_constructive": output["rsi14_dir"].between(-5.0, 25.0)
            & output["macd_hist_dir_atr"].ge(-0.05),
            "eth_aligned": output["eth_return16_dir"].gt(0.0)
            & output["eth_ema30_sma60_spread_dir_atr"].gt(0.0),
        }
    ).fillna(False)
    output["confluence_vote"] = flags.astype(int).sum(axis=1).astype(float)
    for column in flags.columns:
        output[f"vote_{column}"] = flags[column].astype(float)


def build_features(
    config: Mapping[str, Any],
    phase: str,
    *,
    mutate_after: pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Build one causal feature ledger for a phase.

    ``mutate_after`` is used only by the causality audit: raw BTC and ETH bars
    at or after that open time are multiplied by deterministic constants, while
    feature rows before the boundary must remain byte-equivalent numerically.
    """

    events = load_events(config, phase)
    end_key = {
        "development": "development_end_exclusive",
        "confirmation": "confirmation_end_exclusive",
        "audit": "diagnostic_audit_end_exclusive",
    }[phase]
    end = utc(config["splits"][end_key])
    frame, quality = load_base_until(load_parent_config(), end)
    eth_raw = load_eth_until(config, end)
    if mutate_after is not None:
        boundary = utc(mutate_after)
        btc_mask = frame["open_time"].ge(boundary)
        eth_mask = eth_raw["open_time"].ge(boundary)
        for column, multiplier in (
            ("open", 1.7),
            ("high", 1.9),
            ("low", 0.4),
            ("close", 1.6),
            ("volume", 3.0),
        ):
            frame.loc[btc_mask, column] = frame.loc[btc_mask, column].astype(float) * multiplier
            eth_raw.loc[eth_mask, column] = eth_raw.loc[eth_mask, column].astype(float) * multiplier
    _assert_event_alignment(events, frame)
    market = add_market_features(frame)
    eth = add_eth_features(eth_raw)
    output = events.copy()
    indices = output["signal_i"].astype(int).to_numpy()
    direction = output["direction"].to_numpy(dtype=float)
    _sample_directional(output, market, indices, direction)
    _attach_k1k2(output, market, config)
    _attach_higher_timeframe(output, market)
    _attach_eth(output, market, eth)
    _attach_calendar_and_vote(output)
    expected = sorted(
        {
            column
            for columns in config["feature_contract"]["feature_groups"].values()
            for column in columns
        }
        | {"confluence_vote"}
    )
    missing = sorted(set(expected) - set(output.columns))
    if missing:
        raise RuntimeError(f"registered features missing: {missing}")
    forbidden = sorted(
        column
        for column in expected
        if any(token in column.lower() for token in FORBIDDEN_FEATURE_TOKENS)
    )
    if forbidden:
        raise RuntimeError(f"outcome-like feature names forbidden: {forbidden}")
    finite_rate = output[expected].replace([np.inf, -np.inf], np.nan).notna().mean()
    receipt = {
        "phase": phase,
        "events": len(output),
        "first_signal": output["signal_time"].min(),
        "last_signal": output["signal_time"].max(),
        "features": len(expected),
        "minimum_feature_finite_rate": float(finite_rate.min()),
        "minimum_feature_finite_name": str(finite_rate.idxmin()),
        "btc_source": quality,
        "eth_rows_read": len(eth_raw),
        "eth_first": eth_raw["open_time"].min(),
        "eth_last": eth_raw["open_time"].max(),
        "holdout_rows_read": 0,
    }
    return market, output.replace([np.inf, -np.inf], np.nan), receipt


def variant_features(config: Mapping[str, Any], variant: Mapping[str, Any]) -> list[str]:
    groups = config["feature_contract"]["feature_groups"]
    columns: list[str] = []
    for group in variant.get("groups", []):
        columns.extend(map(str, groups[str(group)]))
    return list(dict.fromkeys(columns))


def model_params(config: Mapping[str, Any]) -> dict[str, Any]:
    row = config["model"]
    seed = int(row["seed"])
    return {
        "objective": str(row["objective"]),
        "alpha": float(row["alpha"]),
        "n_estimators": int(row["n_estimators"]),
        "learning_rate": float(row["learning_rate"]),
        "num_leaves": int(row["num_leaves"]),
        "max_depth": int(row["max_depth"]),
        "min_child_samples": int(row["min_child_samples"]),
        "reg_lambda": float(row["reg_lambda"]),
        "colsample_bytree": float(row["colsample_bytree"]),
        "subsample": float(row["subsample"]),
        "verbosity": -1,
        "deterministic": bool(row["deterministic"]),
        "force_col_wise": bool(row["force_col_wise"]),
        "n_jobs": int(row["num_threads"]),
        "random_state": seed,
        "data_random_seed": seed,
        "feature_fraction_seed": seed,
        "bagging_seed": seed,
        "extra_seed": seed,
    }


def matrix(
    events: pd.DataFrame,
    columns: list[str],
    medians: Mapping[str, float] | None = None,
) -> tuple[pd.DataFrame, dict[str, float]]:
    values = events[columns].replace([np.inf, -np.inf], np.nan).astype(float)
    if medians is None:
        learned = {}
        for column in columns:
            value = float(values[column].median())
            learned[column] = value if np.isfinite(value) else 0.0
    else:
        learned = {column: float(medians[column]) for column in columns}
    return values.fillna(learned).fillna(0.0), learned


def _fit_variant(
    train: pd.DataFrame,
    config: Mapping[str, Any],
    variant: Mapping[str, Any],
) -> tuple[Any, dict[str, Any], np.ndarray]:
    """Fit one registered score on past rows and return train scores/contract."""

    kind = str(variant["kind"])
    if kind == "single_score":
        score_column = str(variant.get("score_column", variant["id"]))
        scores = train[score_column].to_numpy(dtype=float)
        return None, {"score_column": score_column, "feature_names": [], "medians": {}}, scores
    columns = variant_features(config, variant)
    x_train, medians = matrix(train, columns)
    lower = float(train["net_return"].quantile(0.01))
    upper = float(train["net_return"].quantile(0.99))
    target = train["net_return"].clip(lower, upper)
    model = lgb.LGBMRegressor(**model_params(config))
    model.fit(x_train, target)
    scores = model.predict(x_train)
    contract = {
        "feature_names": columns,
        "medians": medians,
        "target_clip_lower": lower,
        "target_clip_upper": upper,
    }
    return model, contract, np.asarray(scores, dtype=float)


def _predict_variant(
    events: pd.DataFrame,
    variant: Mapping[str, Any],
    model: Any,
    contract: Mapping[str, Any],
) -> np.ndarray:
    kind = str(variant["kind"])
    if kind == "single_score":
        return events[str(contract["score_column"])].to_numpy(dtype=float)
    x, _ = matrix(events, list(contract["feature_names"]), contract["medians"])
    return np.asarray(model.predict(x), dtype=float)


def _auc(events: pd.DataFrame, scores: np.ndarray) -> float:
    labels = events["net_return"].gt(0.0).astype(int).to_numpy()
    if len(np.unique(labels)) < 2:
        return float("nan")
    return float(roc_auc_score(labels, scores))


def _fold_metrics(pool: pd.DataFrame, selected: pd.DataFrame, scores: np.ndarray) -> dict[str, Any]:
    q90 = float(np.nanquantile(scores, 0.9))
    top = pool.loc[scores >= q90]
    base = metrics(selected)
    return {
        **base,
        "pool_events": len(pool),
        "selection_rate": float(len(selected) / len(pool)) if len(pool) else np.nan,
        "auc_profit": _auc(pool, scores),
        "evaluation_top_decile_events": len(top),
        "evaluation_top_decile_net_bp": float(top["net_return"].mean() * 1e4)
        if len(top)
        else np.nan,
    }


def development_oof(
    events: pd.DataFrame,
    config: Mapping[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Score every registered variant on expanding-time 2023 OOF folds."""

    score_rows: list[pd.DataFrame] = []
    fold_rows: list[dict[str, Any]] = []
    purge = pd.Timedelta(
        minutes=int(config["splits"]["purge_bars_at_boundaries"])
        * int(config["splits"]["bar_minutes"])
    )
    quantile = float(config["score_gate"]["training_quantile"])
    for fold in config["splits"]["development_folds"]:
        fold_id = str(fold["fold"])
        test_start = utc(fold["test_start_inclusive"])
        test_end = utc(fold["test_end_exclusive"])
        train = events.loc[
            events["entry_time"].ge(utc(fold["train_start_inclusive"]))
            & events["entry_time"].lt(test_start - purge)
        ].copy()
        test = events.loc[
            events["entry_time"].ge(test_start) & events["entry_time"].lt(test_end)
        ].copy()
        if train.empty or test.empty:
            raise RuntimeError(f"empty train/test fold {fold_id}")
        for variant in config["candidate_variants"]:
            model, contract, train_scores = _fit_variant(train, config, variant)
            test_scores = _predict_variant(test, variant, model, contract)
            threshold = float(np.nanquantile(train_scores, quantile))
            selected_mask = test_scores >= threshold
            selected = test.loc[selected_mask].copy()
            scored = test.copy()
            scored["fold"] = fold_id
            scored["variant_id"] = str(variant["id"])
            scored["model_score"] = test_scores
            scored["score_threshold"] = threshold
            scored["selected"] = selected_mask
            score_rows.append(scored)
            fold_rows.append(
                {
                    "fold": fold_id,
                    "variant_id": str(variant["id"]),
                    "kind": str(variant["kind"]),
                    "feature_count": len(variant_features(config, variant)),
                    "train_events": len(train),
                    "test_events": len(test),
                    "threshold": threshold,
                    **_fold_metrics(test, selected, test_scores),
                }
            )
    return pd.concat(score_rows, ignore_index=True), pd.DataFrame(fold_rows)


def familywise_oof_p(
    scored: pd.DataFrame,
    variants: Iterable[str],
    *,
    resamples: int,
    seed: int,
) -> dict[str, float]:
    """Max-statistic p-values for all fixed OOF selection masks.

    Returns are centered within each chronological fold and shuffled only
    within that fold. Every registered variant participates in the null max,
    so selecting the best observed feature bundle is multiplicity-adjusted.
    """

    variant_ids = list(variants)
    base = scored.loc[scored["variant_id"].eq(variant_ids[0])].copy()
    base["key"] = base["fold"].astype(str) + "|" + base["setup_id"].astype(str)
    base = base.set_index("key", drop=False)
    centered = np.zeros(len(base), dtype=float)
    fold_positions: list[np.ndarray] = []
    for _, group in base.groupby("fold", sort=True):
        positions = base.index.get_indexer(group.index)
        values = group["net_return"].to_numpy(dtype=float) * 1e4
        centered[positions] = values - float(values.mean())
        fold_positions.append(positions)
    masks: list[np.ndarray] = []
    observed: list[float] = []
    for variant_id in variant_ids:
        part = scored.loc[scored["variant_id"].eq(variant_id)].copy()
        part["key"] = part["fold"].astype(str) + "|" + part["setup_id"].astype(str)
        aligned = part.set_index("key").reindex(base.index)
        if aligned["selected"].isna().any():
            raise RuntimeError(f"OOF key mismatch for {variant_id}")
        mask = aligned["selected"].astype(bool).to_numpy()
        masks.append(mask)
        observed.append(float(centered[mask].mean()) if mask.any() else -np.inf)
    rng = np.random.default_rng(seed)
    exceed = np.zeros(len(variant_ids), dtype=int)
    for _ in range(resamples):
        shuffled = centered.copy()
        for positions in fold_positions:
            shuffled[positions] = rng.permutation(shuffled[positions])
        max_null = max(
            float(shuffled[mask].mean()) if mask.any() else -np.inf for mask in masks
        )
        exceed += max_null >= np.asarray(observed) - 1e-12
    return {
        variant_id: float((count + 1) / (resamples + 1))
        for variant_id, count in zip(variant_ids, exceed)
    }


def summarize_development(
    scored: pd.DataFrame,
    folds: pd.DataFrame,
    config: Mapping[str, Any],
) -> pd.DataFrame:
    variants = [str(row["id"]) for row in config["candidate_variants"]]
    p_values = familywise_oof_p(
        scored,
        variants,
        resamples=int(config["development_nomination"]["familywise_permutation_resamples"]),
        seed=int(config["development_nomination"]["seed"]),
    )
    rows: list[dict[str, Any]] = []
    for variant_id in variants:
        part = folds.loc[folds["variant_id"].eq(variant_id)].copy()
        selected = scored.loc[
            scored["variant_id"].eq(variant_id) & scored["selected"].astype(bool)
        ]
        values = part["mean_net_bp"].to_numpy(dtype=float)
        eligible = bool(
            len(selected)
            >= int(config["development_nomination"]["minimum_selected_events_total"])
            and int(part["events"].min())
            >= int(config["development_nomination"]["minimum_selected_events_per_fold"])
        )
        rows.append(
            {
                "variant_id": variant_id,
                "kind": str(part["kind"].iloc[0]),
                "feature_count": int(part["feature_count"].iloc[0]),
                "eligible": eligible,
                "selected_events": len(selected),
                "selection_rate": float(len(selected) / part["test_events"].sum()),
                "mean_net_bp": float(selected["net_return"].mean() * 1e4),
                "profit_factor": float(metrics(selected)["profit_factor"]),
                "win_rate": float(selected["net_return"].gt(0.0).mean()),
                "mean_auc_profit": float(part["auc_profit"].mean()),
                "mean_evaluation_top_decile_net_bp": float(
                    part["evaluation_top_decile_net_bp"].mean()
                ),
                "positive_fold_count": int(np.count_nonzero(values > 0.0)),
                "worst_fold_net_bp": float(np.min(values)),
                "median_fold_net_bp": float(np.median(values)),
                "fold_std_net_bp": float(np.std(values, ddof=0)),
                "familywise_score_p_one_sided": p_values[variant_id],
            }
        )
    summary = pd.DataFrame(rows)
    return summary.sort_values(
        [
            "eligible",
            "positive_fold_count",
            "worst_fold_net_bp",
            "median_fold_net_bp",
            "mean_net_bp",
            "variant_id",
        ],
        ascending=[False, False, False, False, False, True],
        kind="mergesort",
    ).reset_index(drop=True)


def _variant_by_id(config: Mapping[str, Any], variant_id: str) -> dict[str, Any]:
    for variant in config["candidate_variants"]:
        if str(variant["id"]) == variant_id:
            return dict(variant)
    raise KeyError(variant_id)


def fit_final_contracts(
    events: pd.DataFrame,
    config: Mapping[str, Any],
    variant_ids: Iterable[str],
) -> dict[str, Any]:
    """Fit committed 2023 research scorers for nomination and comparisons."""

    MODELS.mkdir(parents=True, exist_ok=True)
    contracts: dict[str, Any] = {}
    quantile = float(config["score_gate"]["training_quantile"])
    for variant_id in dict.fromkeys(variant_ids):
        variant = _variant_by_id(config, variant_id)
        model, contract, scores = _fit_variant(events, config, variant)
        threshold = float(np.nanquantile(scores, quantile))
        record = {
            "variant_id": variant_id,
            "kind": str(variant["kind"]),
            "groups": list(variant.get("groups", [])),
            **contract,
            "score_quantile": quantile,
            "score_threshold": threshold,
            "train_events": len(events),
        }
        if model is not None:
            path = MODELS / f"{variant_id}.txt"
            model.booster_.save_model(str(path))
            record["model_path"] = str(path.relative_to(ROOT))
            record["model_sha256"] = sha256_file(path)
            importance = pd.DataFrame(
                {
                    "variant_id": variant_id,
                    "feature": list(contract["feature_names"]),
                    "split_importance": model.booster_.feature_importance(
                        importance_type="split"
                    ),
                    "gain_importance": model.booster_.feature_importance(
                        importance_type="gain"
                    ),
                }
            ).sort_values(["gain_importance", "feature"], ascending=[False, True])
            write_csv(importance, RESULTS / f"feature_importance_{variant_id}.csv")
        contracts[variant_id] = record
    return contracts


def causality_mutation_audit(
    config: Mapping[str, Any],
    original: pd.DataFrame,
    phase: str,
    boundary: pd.Timestamp,
) -> dict[str, Any]:
    """Mutate every source row after one boundary and compare earlier features."""

    _, mutated, _ = build_features(config, phase, mutate_after=boundary)
    feature_names = sorted(
        {
            column
            for values in config["feature_contract"]["feature_groups"].values()
            for column in values
        }
        | {"confluence_vote"}
    )
    left = original.loc[original["signal_time"].lt(boundary)].set_index("setup_id")
    right = mutated.loc[mutated["signal_time"].lt(boundary)].set_index("setup_id")
    if not left.index.equals(right.index):
        raise RuntimeError("future mutation changed pre-boundary event identity")
    a = left[feature_names].to_numpy(dtype=float)
    b = right[feature_names].to_numpy(dtype=float)
    finite = np.isfinite(a) & np.isfinite(b)
    mismatch_nan = np.logical_xor(np.isfinite(a), np.isfinite(b))
    maximum = float(np.max(np.abs(a[finite] - b[finite]))) if finite.any() else 0.0
    passed = bool(not mismatch_nan.any() and maximum <= 1e-12)
    if not passed:
        raise RuntimeError(f"future mutation changed causal feature matrix: {maximum}")
    return {
        "boundary": utc(boundary),
        "events_compared": len(left),
        "features_compared": len(feature_names),
        "maximum_absolute_difference": maximum,
        "finite_pattern_mismatches": int(mismatch_nan.sum()),
        "passed": passed,
    }


def selection_phase(config: dict[str, Any]) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    market, events, source = build_features(config, "development")
    mutation = causality_mutation_audit(
        config, events, "development", utc("2023-07-01T00:00:00Z")
    )
    scored, folds = development_oof(events, config)
    summary = summarize_development(scored, folds, config)
    eligible = summary.loc[summary["eligible"].astype(bool)]
    if eligible.empty:
        raise RuntimeError("no sample-eligible development variant")
    nominated = str(eligible.iloc[0]["variant_id"])
    contracts = fit_final_contracts(
        events,
        config,
        [nominated, "signal_score", "rule_confluence_vote", "legacy_28"],
    )
    write_csv(events, RESULTS / "development_feature_ledger.csv.gz")
    write_csv(
        scored[
            [
                "fold",
                "variant_id",
                "setup_id",
                "signal_time",
                "entry_time",
                "direction",
                "net_return",
                "model_score",
                "score_threshold",
                "selected",
            ]
        ],
        RESULTS / "development_oof_scores.csv.gz",
    )
    write_csv(folds, RESULTS / "development_fold_metrics.csv")
    write_csv(summary, RESULTS / "development_variant_summary.csv")
    write_json(MODEL_CONTRACT, {"nominated_variant": nominated, "contracts": contracts})
    receipt = {
        "phase": "development_selection_complete_confirmation_unopened",
        "config_sha256": sha256_file(CONFIG_PATH),
        "script_sha256": sha256_file(SCRIPT_PATH),
        "source": source,
        "causality_mutation": mutation,
        "candidate_variants": len(config["candidate_variants"]),
        "nominated_variant": nominated,
        "nominated_development": summary.iloc[0].to_dict(),
        "signal_score_development": summary.loc[
            summary["variant_id"].eq("signal_score")
        ].iloc[0].to_dict(),
        "legacy_28_development": summary.loc[
            summary["variant_id"].eq("legacy_28")
        ].iloc[0].to_dict(),
        "model_contract_sha256": sha256_file(MODEL_CONTRACT),
        "contract_set_sha256": _json_hash(contracts),
        "confirmation_rows_read": 0,
        "audit_rows_read": 0,
        "holdout_rows_read": 0,
        "interpretation": "retrospective hypothesis nomination only",
    }
    write_json(SELECTION_RECEIPT, receipt)
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))


def _assert_committed(paths: Iterable[Path], label: str) -> None:
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
        raise RuntimeError(f"{label} artifacts must be committed and clean: {dirty}")


def _assert_selection_committed() -> tuple[dict[str, Any], dict[str, Any]]:
    receipt = json.loads(SELECTION_RECEIPT.read_text(encoding="utf-8"))
    contract = json.loads(MODEL_CONTRACT.read_text(encoding="utf-8"))
    paths = [CONFIG_PATH, SCRIPT_PATH, SELECTION_RECEIPT, MODEL_CONTRACT]
    for record in contract["contracts"].values():
        if "model_path" in record:
            paths.append(ROOT / str(record["model_path"]))
        importance = RESULTS / f"feature_importance_{record['variant_id']}.csv"
        if importance.exists():
            paths.append(importance)
    _assert_committed(paths, "selection")
    expected = {
        "config_sha256": sha256_file(CONFIG_PATH),
        "script_sha256": sha256_file(SCRIPT_PATH),
        "model_contract_sha256": sha256_file(MODEL_CONTRACT),
    }
    for key, value in expected.items():
        if str(receipt.get(key)) != value:
            raise RuntimeError(f"selection {key} drift")
    return receipt, contract


def score_from_contract(
    events: pd.DataFrame,
    contract: Mapping[str, Any],
) -> np.ndarray:
    kind = str(contract["kind"])
    if kind == "single_score":
        return events[str(contract["score_column"])].to_numpy(dtype=float)
    path = ROOT / str(contract["model_path"])
    if sha256_file(path) != str(contract["model_sha256"]):
        raise RuntimeError(f"model hash drift: {contract['variant_id']}")
    booster = lgb.Booster(model_file=str(path))
    x, _ = matrix(events, list(contract["feature_names"]), contract["medians"])
    return np.asarray(booster.predict(x), dtype=float)


def evaluate_contract(
    events: pd.DataFrame,
    contract: Mapping[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    scores = score_from_contract(events, contract)
    selected_mask = scores >= float(contract["score_threshold"])
    scored = events.copy()
    scored["variant_id"] = str(contract["variant_id"])
    scored["model_score"] = scores
    scored["score_threshold"] = float(contract["score_threshold"])
    scored["selected"] = selected_mask
    selected = scored.loc[selected_mask].copy()
    return scored, selected, _fold_metrics(events, selected, scores)


def _phase_half_table(events: pd.DataFrame) -> pd.DataFrame:
    labels = pd.to_datetime(events["entry_time"], utc=True).map(
        lambda stamp: f"{stamp.year}H{1 if stamp.month <= 6 else 2}"
    )
    return pd.DataFrame(
        [{"fold": fold, **metrics(events.loc[labels.eq(fold)])} for fold in sorted(labels.unique())]
    )


def control_configuration(config: Mapping[str, Any]) -> dict[str, Any]:
    compatibility = load_v2_config()
    compatibility["matched_control"] = dict(config["matched_control"])
    return compatibility


def _matched_control_metrics(
    selected: pd.DataFrame,
    frame: pd.DataFrame,
    config: Mapping[str, Any],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    controls, pairs = matched_controls(
        selected,
        frame,
        control_configuration(config),
        policy=str(config["execution_frozen"]["runner_policy"]),
        start=start,
        end=end,
    )
    matched = pairs.loc[pairs["match_status"].eq("matched_exact")]
    excess = matched["paired_excess_return"].to_numpy(dtype=float)
    assignment_means = (
        controls.groupby("assignment", sort=True)["net_return"].mean().mul(1e4).to_dict()
        if len(controls)
        else {}
    )
    result = {
        "matched_events": len(matched),
        "matched_control_excess_bp": float(np.mean(excess) * 1e4) if len(excess) else np.nan,
        "paired_control_signflip_p_one_sided": float(
            signflip_p(excess, resamples=100_000, seed=20260904)
        )
        if len(excess)
        else np.nan,
        "control_assignment_mean_net_bp": assignment_means,
        "all_eight_control_assignments_beaten": bool(
            len(assignment_means) == int(config["matched_control"]["controls_per_event"])
            and all(
                float(selected["net_return"].mean() * 1e4) > float(value)
                for value in assignment_means.values()
            )
        ),
    }
    return controls, pairs, result


def confirmation_phase(config: dict[str, Any]) -> None:
    selection, contract_set = _assert_selection_committed()
    frame, events, source = build_features(config, "confirmation")
    variants = list(
        dict.fromkeys(
            [
                str(selection["nominated_variant"]),
                "signal_score",
                "rule_confluence_vote",
                "legacy_28",
            ]
        )
    )
    scored_parts: list[pd.DataFrame] = []
    selected_parts: dict[str, pd.DataFrame] = {}
    comparison: list[dict[str, Any]] = []
    for variant_id in variants:
        scored, selected, row = evaluate_contract(
            events, contract_set["contracts"][variant_id]
        )
        scored_parts.append(scored)
        selected_parts[variant_id] = selected
        comparison.append({"variant_id": variant_id, **row})
    nominated = str(selection["nominated_variant"])
    selected = selected_parts[nominated]
    start = utc(config["splits"]["confirmation_start_inclusive"])
    end = utc(config["splits"]["confirmation_end_exclusive"])
    controls, pairs, control_metrics = _matched_control_metrics(
        selected, frame, config, start, end
    )
    nominated_scores = scored_parts[0]["model_score"].to_numpy(dtype=float)
    selected_metrics = {
        **next(row for row in comparison if row["variant_id"] == nominated),
        "score_permutation_p_one_sided": score_permutation_p(
            events.assign(model_score=nominated_scores),
            selected,
            resamples=100_000,
            seed=20260904,
        ),
        **control_metrics,
    }
    half = _phase_half_table(selected)
    signal_metrics = next(row for row in comparison if row["variant_id"] == "signal_score")
    legacy_metrics = next(row for row in comparison if row["variant_id"] == "legacy_28")
    gate = config["confirmation_gate"]
    gates = {
        "minimum_selected_events": len(selected) >= int(gate["minimum_selected_events"]),
        "mean_net_positive": float(selected_metrics["mean_net_bp"]) > float(gate["mean_net_bp_gt"]),
        "profit_factor": float(selected_metrics["profit_factor"]) > float(gate["profit_factor_gt"]),
        "score_permutation_p": float(selected_metrics["score_permutation_p_one_sided"])
        < float(gate["score_permutation_p_lt"]),
        "paired_control_p": float(selected_metrics["paired_control_signflip_p_one_sided"])
        < float(gate["paired_control_p_lt"]),
        "all_eight_controls_beaten": bool(
            selected_metrics["all_eight_control_assignments_beaten"]
        ),
        "both_halfyears_positive": bool(half["mean_net_bp"].gt(0.0).all()),
        "beat_signal_score": float(selected_metrics["mean_net_bp"])
        > float(signal_metrics["mean_net_bp"]),
        "beat_legacy_28": float(selected_metrics["mean_net_bp"])
        > float(legacy_metrics["mean_net_bp"]),
    }
    gates["all_pass"] = bool(all(gates.values()))
    write_csv(events, RESULTS / "confirmation_feature_ledger.csv.gz")
    write_csv(pd.concat(scored_parts, ignore_index=True), RESULTS / "confirmation_scored_variants.csv.gz")
    write_csv(pd.DataFrame(comparison), RESULTS / "confirmation_variant_comparison.csv")
    write_csv(selected, RESULTS / "confirmation_selected_trades.csv.gz")
    write_csv(half, RESULTS / "confirmation_selected_folds.csv")
    write_csv(controls, RESULTS / "confirmation_controls.csv.gz")
    write_csv(pairs, RESULTS / "confirmation_control_pairs.csv")
    receipt = {
        "phase": "exact_2024_replay_complete_audit_unopened",
        "config_sha256": sha256_file(CONFIG_PATH),
        "script_sha256": sha256_file(SCRIPT_PATH),
        "selection_receipt_sha256": sha256_file(SELECTION_RECEIPT),
        "model_contract_sha256": sha256_file(MODEL_CONTRACT),
        "nominated_variant": nominated,
        "source": source,
        "metrics": selected_metrics,
        "halfyear_metrics": half.to_dict("records"),
        "comparison": comparison,
        "gates": gates,
        "audit_rows_read": 0,
        "holdout_rows_read": 0,
        "production_eligible": False,
        "interpretation": "exact preregistered replay on previously accessed lineage; not pristine confirmation",
    }
    write_json(CONFIRMATION_RECEIPT, receipt)
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))


def _assert_confirmation_committed() -> tuple[dict[str, Any], dict[str, Any]]:
    selection, contract = _assert_selection_committed()
    confirmation = json.loads(CONFIRMATION_RECEIPT.read_text(encoding="utf-8"))
    _assert_committed([CONFIRMATION_RECEIPT], "confirmation")
    expected = {
        "config_sha256": sha256_file(CONFIG_PATH),
        "script_sha256": sha256_file(SCRIPT_PATH),
        "selection_receipt_sha256": sha256_file(SELECTION_RECEIPT),
        "model_contract_sha256": sha256_file(MODEL_CONTRACT),
    }
    for key, value in expected.items():
        if str(confirmation.get(key)) != value:
            raise RuntimeError(f"confirmation {key} drift")
    if str(confirmation["nominated_variant"]) != str(selection["nominated_variant"]):
        raise RuntimeError("confirmation nomination drift")
    return confirmation, contract


def _feature_quartiles(
    events: pd.DataFrame,
    feature_names: list[str],
    maximum_features: int = 20,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for feature in feature_names[:maximum_features]:
        values = pd.to_numeric(events[feature], errors="coerce")
        try:
            bins = pd.qcut(values, q=4, labels=False, duplicates="drop")
        except ValueError:
            continue
        for quartile in sorted(pd.Series(bins).dropna().unique()):
            subset = events.loc[pd.Series(bins, index=events.index).eq(quartile)]
            if subset.empty:
                continue
            rows.append(
                {
                    "feature": feature,
                    "quartile": int(quartile) + 1,
                    "events": len(subset),
                    "mean_net_bp": float(subset["net_return"].mean() * 1e4),
                    "win_rate": float(subset["net_return"].gt(0.0).mean()),
                }
            )
    return pd.DataFrame(rows)


def audit_phase(config: dict[str, Any]) -> None:
    confirmation, contract_set = _assert_confirmation_committed()
    frame, events, source = build_features(config, "audit")
    variants = list(
        dict.fromkeys(
            [
                str(confirmation["nominated_variant"]),
                "signal_score",
                "rule_confluence_vote",
                "legacy_28",
            ]
        )
    )
    scored_parts: list[pd.DataFrame] = []
    selected_parts: dict[str, pd.DataFrame] = {}
    comparison: list[dict[str, Any]] = []
    for variant_id in variants:
        scored, selected, row = evaluate_contract(
            events, contract_set["contracts"][variant_id]
        )
        scored_parts.append(scored)
        selected_parts[variant_id] = selected
        comparison.append({"variant_id": variant_id, **row})
    nominated = str(confirmation["nominated_variant"])
    selected = selected_parts[nominated]
    start = utc(config["splits"]["diagnostic_audit_start_inclusive"])
    end = utc(config["splits"]["diagnostic_audit_end_exclusive"])
    controls, pairs, control_metrics = _matched_control_metrics(
        selected, frame, config, start, end
    )
    nominated_scores = scored_parts[0]["model_score"].to_numpy(dtype=float)
    selected_metrics = {
        **next(row for row in comparison if row["variant_id"] == nominated),
        "score_permutation_p_one_sided": score_permutation_p(
            events.assign(model_score=nominated_scores),
            selected,
            resamples=100_000,
            seed=20260905,
        ),
        **control_metrics,
    }
    half = _phase_half_table(selected)
    failures = corrected_failure_mechanics(selected)
    model_record = contract_set["contracts"][nominated]
    feature_names = list(model_record.get("feature_names", []))
    if feature_names:
        importance_path = RESULTS / f"feature_importance_{nominated}.csv"
        importance = pd.read_csv(importance_path)
        ordered = importance.sort_values("gain_importance", ascending=False)["feature"].tolist()
    else:
        ordered = [str(model_record.get("score_column", "signal_score"))]
    quartiles = _feature_quartiles(events, ordered)
    write_csv(events, RESULTS / "audit_feature_ledger.csv.gz")
    write_csv(pd.concat(scored_parts, ignore_index=True), RESULTS / "audit_scored_variants.csv.gz")
    write_csv(pd.DataFrame(comparison), RESULTS / "audit_variant_comparison.csv")
    write_csv(selected, RESULTS / "audit_selected_trades.csv.gz")
    write_csv(half, RESULTS / "audit_selected_folds.csv")
    write_csv(controls, RESULTS / "audit_controls.csv.gz")
    write_csv(pairs, RESULTS / "audit_control_pairs.csv")
    write_csv(failures, RESULTS / "audit_failure_mechanics.csv.gz")
    write_csv(quartiles, RESULTS / "audit_feature_quartiles.csv")
    receipt = {
        "phase": "already_seen_2025_to_2026p1_diagnostic_audit_complete",
        "config_sha256": sha256_file(CONFIG_PATH),
        "script_sha256": sha256_file(SCRIPT_PATH),
        "selection_receipt_sha256": sha256_file(SELECTION_RECEIPT),
        "confirmation_receipt_sha256": sha256_file(CONFIRMATION_RECEIPT),
        "model_contract_sha256": sha256_file(MODEL_CONTRACT),
        "nominated_variant": nominated,
        "source": source,
        "metrics": selected_metrics,
        "halfyear_metrics": half.to_dict("records"),
        "comparison": comparison,
        "failure_categories": (
            failures.groupby("failure_category", sort=True)
            .agg(events=("setup_id", "size"), mean_net_bp=("net_return", lambda x: float(x.mean() * 1e4)))
            .reset_index()
            .to_dict("records")
            if len(failures)
            else []
        ),
        "holdout_rows_read": 0,
        "production_eligible": False,
        "interpretation": "transport and failure diagnostic only; lineage was already exposed",
    }
    write_json(AUDIT_RECEIPT, receipt)
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["select", "confirm", "audit"], required=True)
    args = parser.parse_args()
    config = load_config()
    if args.phase == "select":
        selection_phase(config)
    elif args.phase == "confirm":
        confirmation_phase(config)
    else:
        audit_phase(config)


if __name__ == "__main__":
    main()
