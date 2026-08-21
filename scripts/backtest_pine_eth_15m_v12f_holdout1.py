#!/usr/bin/env python3
"""Consume the first owner-approved V12F ETH 15m holdout exactly once.

This runner is intentionally narrower than the development replay.  It reads
only the bounded OHLCV prefix ending at ``2026-08-21T00:00:00Z`` and evaluates
only the already frozen V9 comparator and V12F six-MA W8 full-state gate.  No
parameter search, V12E entry-only arm, TBSL arm, model training, promotion or
live action is reachable from this file.

At signal bar ``t`` every signal and gate feature uses OHLCV through ``t``.
The six-MA factor uses close-derived SMA/EMA 20/60/120 values, pairwise crosses
at ``t``/``t-1`` and the eight-bar window ``[t-7, t]``.  Future rows are used
only for the frozen next-open execution replay and matched-control outcomes.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scipy

from scripts.backtest_pine_eth_15m_v12_preholdout import (
    BacktestArm,
    _json_safe,
    _ranking_metrics,
    _sha256,
    _standard_arm,
    build_v12_feature_frame,
)
from scripts.research_pine_eth_15m import (
    INITIAL_CAPITAL,
    Period,
    block_signflip,
    build_matched_controls,
    current_commit,
    exact_execution,
    pair_controls,
    sha256_bounded_frame,
    summarize,
)
from src.data.fetch_okx import API as OKX_HISTORY_API
from src.data.fetch_okx import PAGE_LIMIT as OKX_PAGE_LIMIT
from src.data.fetch_okx import _request as okx_request
from yoyo.layers.l3_backtest.pine_allin_v7 import (
    SignalParameters,
    max_drawdown,
    simulate_symbol,
)


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
RESULTS = EXPERIMENT / "results"
CHARTS = RESULTS / "charts"
PINE_DIR = EXPERIMENT / "pine"
DATA_PATH = PROJECT / "data/kline_deep/okx_ETH_USDT_SWAP_15m_158499.csv"

SUMMARY_OUTPUT = RESULTS / "v12f_holdout1_recent6m_summary.json"
TRADES_OUTPUT = RESULTS / "v12f_holdout1_recent6m_trades.csv"
CONTROLS_OUTPUT = RESULTS / "v12f_holdout1_recent6m_controls.csv"
PAIRS_OUTPUT = RESULTS / "v12f_holdout1_recent6m_pairs.csv"
SENSITIVITY_OUTPUT = RESULTS / "v12f_holdout1_recent6m_control_sensitivity.csv"
MONTHLY_OUTPUT = RESULTS / "v12f_holdout1_recent6m_monthly.csv"
EQUITY_OUTPUT = RESULTS / "v12f_holdout1_recent6m_equity.csv"
CHART_OUTPUT = CHARTS / "v12f_holdout1_recent6m_equity_monthly.png"
LEDGER_OUTPUT = RESULTS / "v12f_holdout1_consumption.json"
CONFIG_OUTPUT = EXPERIMENT / "v12f_holdout1_config.json"

REQUESTED_START = pd.Timestamp("2026-02-21T00:00:00Z")
REQUESTED_END = pd.Timestamp("2026-08-21T00:00:00Z")
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
BAR_DURATION = pd.Timedelta(minutes=15)

PERIODS = (
    Period("requested_recent_6m", REQUESTED_START, REQUESTED_END),
    Period("protected_holdout_fresh_start", HOLDOUT_START, REQUESTED_END),
)

OWNER_APPROVAL = {
    "approved": True,
    "conversation_thread_id": "01a01fb5-7d9d-7511-9184-cdf0ce4db62e",
    "approval_date_asia_shanghai": "2026-08-21",
    "request_text": (
        "批准 V12F 配置第 1 次消耗 holdout，区间 2026-02-21 至 2026-08-21"
    ),
    "owner_reply": "批准",
    "consumption_number": 1,
    "scope": "V12F frozen configuration with frozen V9 comparator only",
}

EXPECTED_V9_PINE_SHA256 = (
    "6465fa80f89907b2ed584085960f355f331de62e3c38ebf65f3065c57873dfe9"
)
EXPECTED_V12F_PINE_SHA256 = (
    "9e03c2959e403632a8db06c66ee43487d7388e0dfdaf31abe5ae32218c7567de"
)
FROZEN_CONFIG_SHA256 = (
    "97cb40d4b8dfd792771f000fbd0356922dac9c136e45b79219e85548f1b42eb4"
)

CONTROLS_PER_TRADE = 3
CONTROL_ASSIGNMENT_SEEDS = 32
SIGNFLIP_RESAMPLES = 20_000
ROUND_TRIP_COST = 0.002
MIN_WARMUP_BARS = 400
EXPECTED_RUNTIME_VERSIONS = {
    "numpy": "2.0.2",
    "pandas": "2.3.3",
    "matplotlib": "3.9.4",
    "scipy": "1.13.1",
}

RUNTIME_PROVENANCE_PATHS = (
    Path("scripts/backtest_pine_eth_15m_v12f_holdout1.py"),
    Path("scripts/backtest_pine_eth_15m_v12_preholdout.py"),
    Path("scripts/research_pine_eth_15m.py"),
    Path("yoyo/layers/l3_backtest/pine_allin_v7.py"),
    Path("yoyo/layers/l2_judgment/pine_cross_features.py"),
    Path("yoyo/layers/l2_judgment/features.py"),
    Path("yoyo/datasets/ma_rope_filter.py"),
    Path("yoyo/data/indicators.py"),
    Path("yoyo/evaluation/permutation.py"),
    Path("src/data/fetch_okx.py"),
    Path("src/data/bars.py"),
    Path("experiments/active/exp-pine-eth-15m-v1/pine/allin_eth_15m_v9_research.pine"),
    Path(
        "experiments/active/exp-pine-eth-15m-v1/pine/"
        "allin_eth_15m_v12f_ma6_w8_full_gate_paper.pine"
    ),
)

MATERIAL_OUTPUTS = (
    CONFIG_OUTPUT,
    SUMMARY_OUTPUT,
    TRADES_OUTPUT,
    CONTROLS_OUTPUT,
    PAIRS_OUTPUT,
    SENSITIVITY_OUTPUT,
    MONTHLY_OUTPUT,
    EQUITY_OUTPUT,
    CHART_OUTPUT,
)


@dataclass(frozen=True)
class RunOutput:
    """One approved arm/period result and its audit tables."""

    trades: pd.DataFrame
    equity: pd.DataFrame
    controls: pd.DataFrame
    pairs: pd.DataFrame
    sensitivity: pd.DataFrame
    monthly: pd.DataFrame
    summary: dict[str, Any]


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def frozen_config_contract() -> dict[str, Any]:
    """Return the immutable configuration authorized for consumption #1."""

    return {
        "artifact": "ETH 15m V12F holdout consumption #1",
        "approval": OWNER_APPROVAL,
        "instrument": {
            "research_source": "OKX",
            "symbol": "ETH-USDT-SWAP",
            "bar_minutes": 15,
            "venue_note": "research proxy; not claimed bar-identical to TradingView ETHUSDT.P",
            "data_contract": (
                "immutable local deep prefix plus bounded OKX history-candles tail fetched "
                "in memory with after=end_exclusive; no kline file is written"
            ),
            "local_prefix": str(DATA_PATH.relative_to(PROJECT)),
            "tail_endpoint": OKX_HISTORY_API,
            "minimum_warmup_bars_before_requested_start": MIN_WARMUP_BARS,
        },
        "periods": [
            {
                "name": period.name,
                "start_inclusive": period.start.isoformat(),
                "end_exclusive": period.end.isoformat(),
            }
            for period in PERIODS
        ],
        "arms": [
            {
                "name": "v9_frozen_baseline",
                "pine_sha256": EXPECTED_V9_PINE_SHA256,
                "role": "frozen comparator",
            },
            {
                "name": "v12f_ma6_w8_full_gate",
                "pine_sha256": EXPECTED_V12F_PINE_SHA256,
                "role": "single frozen candidate",
            },
        ],
        "execution": {
            "entry": "confirmed signal t; market fill at open t+1",
            "round_trip_cost": ROUND_TRIP_COST,
            "commission_per_side": 0.001,
            "risk_per_trade_percent": 1.0,
            "max_leverage": 13.0,
            "atr_mult": 4.0,
            "max_sl_percent": 3.0,
            "break_even_trigger_percent": 1.5,
            "break_even_offset_percent": 0.1,
            "take_profit_percent": None,
            "cooldown": True,
            "opposite_signal": "reverse",
            "force_close_at_period_end": True,
        },
        "runtime_versions": EXPECTED_RUNTIME_VERSIONS,
        "v12f_gate": {
            "bundle": ["SMA20", "EMA20", "SMA60", "EMA60", "SMA120", "EMA120"],
            "source": "close",
            "directional_pairs": 12,
            "window_bars": 8,
            "window": "[t-7,t]",
            "threshold": 0,
            "future_bars": 0,
            "scope": "full guarded state transition",
        },
        "tradingview_inputs_required": {
            "start_utc": REQUESTED_START.isoformat(),
            "end_exclusive_utc": REQUESTED_END.isoformat(),
            "note": (
                "the frozen Pine file keeps its preholdout defaults; these two inputs must "
                "be set explicitly for the approved replay"
            ),
        },
        "matched_control": {
            "controls_per_trade": CONTROLS_PER_TRADE,
            "strata": "ETH x UTC month x HK 6h x previous-month ATR quintile",
            "copied": "direction x holding horizon x stop x break-even x cost",
            "assignment_seeds": CONTROL_ASSIGNMENT_SEEDS,
            "week_block_signflip_resamples": SIGNFLIP_RESAMPLES,
        },
        "forbidden": [
            "V12E",
            "V12T/TBSL",
            "parameter search",
            "feature selection",
            "training",
            "promotion",
            "deployment",
            "live trading",
        ],
        "protected_segment_semantics": {
            "independent_verdict": "fresh equity, position and cooldown state at holdout start",
            "continuous_diagnostic": (
                "slice of the requested six-month equity path retaining pre-holdout state; "
                "reported separately and not used for candidate selection"
            ),
        },
    }


def approved_arms() -> tuple[BacktestArm, ...]:
    """Expose only the two arms named in the owner's approval."""

    params = SignalParameters(osc_threshold=0.1)
    execution = exact_execution(equity_frequency=None)
    return (
        BacktestArm(
            name="v9_frozen_baseline",
            pine_path=PINE_DIR / "allin_eth_15m_v9_research.pine",
            signal_columns=("v9_long", "v9_short", "v9_score"),
            entry_gate_columns=None,
            params=params,
            execution=execution,
            change_contract="none (frozen comparator)",
            strict_single_variable=True,
        ),
        BacktestArm(
            name="v12f_ma6_w8_full_gate",
            pine_path=PINE_DIR / "allin_eth_15m_v12f_ma6_w8_full_gate_paper.pine",
            signal_columns=("v12f_period_long", "v12f_period_short", "v9_score"),
            entry_gate_columns=None,
            params=params,
            execution=execution,
            change_contract="MA6 W8 cross imbalance >= 0 gates the full guarded state transition",
            strict_single_variable=True,
        ),
    )


def validate_frozen_contract() -> None:
    """Fail before opening holdout data if any approved identity drifted."""

    config_hash = _canonical_sha256(frozen_config_contract())
    if config_hash != FROZEN_CONFIG_SHA256:
        raise RuntimeError(
            "frozen holdout config drifted: "
            f"expected={FROZEN_CONFIG_SHA256} actual={config_hash}"
        )
    arms = approved_arms()
    names = [arm.name for arm in arms]
    if names != ["v9_frozen_baseline", "v12f_ma6_w8_full_gate"]:
        raise RuntimeError(f"unapproved arm set: {names}")
    expected = {
        "v9_frozen_baseline": EXPECTED_V9_PINE_SHA256,
        "v12f_ma6_w8_full_gate": EXPECTED_V12F_PINE_SHA256,
    }
    for arm in arms:
        actual = _sha256(arm.pine_path)
        if actual != expected[arm.name]:
            raise RuntimeError(
                f"Pine hash drift for {arm.name}: expected={expected[arm.name]} actual={actual}"
            )
        if arm.execution.take_profit_percent is not None:
            raise RuntimeError("take-profit/TBSL is outside the approved V12F holdout scope")
    actual_versions = {
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "matplotlib": matplotlib.__version__,
        "scipy": scipy.__version__,
    }
    if actual_versions != EXPECTED_RUNTIME_VERSIONS:
        raise RuntimeError(
            "holdout runtime version drift: "
            f"expected={EXPECTED_RUNTIME_VERSIONS} actual={actual_versions}"
        )


def validate_committed_runtime_provenance() -> None:
    """Require every runtime dependency to be tracked and clean on ``main``."""

    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=PROJECT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if branch != "main":
        raise RuntimeError(f"holdout runner must execute from main, got {branch!r}")
    relative = [str(path) for path in RUNTIME_PROVENANCE_PATHS]
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all", "--", *relative],
        cwd=PROJECT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if status:
        raise RuntimeError(
            "holdout runtime files must be committed and clean before consumption:\n"
            f"{status}"
        )


def _read_local_prefix_before(
    path: Path,
    *,
    end_exclusive: pd.Timestamp,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Materialize local OHLCV only while ``open_time < end_exclusive``."""

    required = {"open_time", "open", "high", "low", "close", "volume"}
    rows: list[dict[str, Any]] = []
    boundary_timestamps_inspected = 0
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = sorted(required - set(reader.fieldnames or []))
        if missing:
            raise RuntimeError(f"missing OHLCV columns in {path}: {missing}")
        for source_row in reader:
            open_time = pd.to_datetime(
                source_row["open_time"],
                utc=True,
                errors="raise",
            )
            if open_time >= end_exclusive:
                # Only the timestamp string is inspected. No out-of-window
                # OHLCV value is converted or materialized.
                boundary_timestamps_inspected += 1
                break
            rows.append(
                {
                    "open_time": open_time,
                    "open": float(source_row["open"]),
                    "high": float(source_row["high"]),
                    "low": float(source_row["low"]),
                    "close": float(source_row["close"]),
                    "volume": float(source_row["volume"]),
                }
            )
    if not rows:
        raise RuntimeError(f"empty approved local prefix: {path}")
    return pd.DataFrame(rows), {
        "local_rows_materialized": len(rows),
        "local_boundary_timestamp_rows_inspected": boundary_timestamps_inspected,
        "local_ohlcv_rows_at_or_after_approved_end_materialized": 0,
    }


def _fetch_approved_tail_memory(
    *,
    start_after: pd.Timestamp,
    end_exclusive: pd.Timestamp,
    request_fn: Any,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Fetch a bounded OKX tail in memory, starting with ``after=end``.

    OKX documents ``after`` as returning records earlier than the supplied
    timestamp.  Each response is checked against both the exclusive approved
    end and the decreasing pagination cursor before any OHLCV value is kept.
    No response row is written to a kline file.
    """

    start_ms = int(start_after.timestamp() * 1_000)
    end_ms = int(end_exclusive.timestamp() * 1_000)
    cursor = end_ms
    rows: dict[int, dict[str, Any]] = {}
    seen_response_timestamps: set[int] = set()
    request_count = 0
    response_rows = 0
    out_of_bounds_rows = 0
    unconfirmed_rows = 0
    reached_local_prefix = False
    while request_count < 200:
        url = (
            f"{OKX_HISTORY_API}?instId=ETH-USDT-SWAP&bar=15m"
            f"&limit={OKX_PAGE_LIMIT}&after={cursor}"
        )
        payload = request_fn(url)
        request_count += 1
        if payload.get("code") != "0":
            raise RuntimeError(f"OKX history-candles error: {payload.get('msg')}")
        page = payload.get("data") or []
        if not page:
            raise RuntimeError("OKX history-candles ended before reaching local prefix")
        response_rows += len(page)
        page_timestamps = [int(row[0]) for row in page]
        if len(set(page_timestamps)) != len(page_timestamps):
            raise RuntimeError("OKX returned duplicate timestamps within one page")
        repeated = seen_response_timestamps.intersection(page_timestamps)
        if repeated:
            raise RuntimeError(
                "OKX pagination repeated timestamps across pages: "
                f"{sorted(repeated)[:5]}"
            )
        seen_response_timestamps.update(page_timestamps)
        out_of_bounds_rows += sum(timestamp >= end_ms for timestamp in page_timestamps)
        if out_of_bounds_rows:
            raise RuntimeError(
                "OKX returned a row at/after the owner-approved exclusive end"
            )
        if any(timestamp >= cursor for timestamp in page_timestamps):
            raise RuntimeError("OKX after= pagination did not stay strictly earlier")
        for response_row in page:
            timestamp = int(response_row[0])
            if timestamp <= start_ms:
                continue
            if len(response_row) > 8 and response_row[8] == "0":
                unconfirmed_rows += 1
                raise RuntimeError(
                    "OKX returned an unconfirmed candle inside the approved historical tail"
                )
            rows[timestamp] = {
                "open_time": pd.to_datetime(timestamp, unit="ms", utc=True),
                "open": float(response_row[1]),
                "high": float(response_row[2]),
                "low": float(response_row[3]),
                "close": float(response_row[4]),
                "volume": float(response_row[5]),
            }
        oldest = min(page_timestamps)
        if oldest <= start_ms:
            reached_local_prefix = True
            break
        if oldest >= cursor:
            raise RuntimeError("OKX pagination cursor made no progress")
        cursor = oldest
    if not reached_local_prefix:
        raise RuntimeError("OKX bounded tail exceeded the 200-page safety limit")
    tail = pd.DataFrame([rows[key] for key in sorted(rows)])
    return tail, {
        "api": OKX_HISTORY_API,
        "pagination_contract": "after=end_exclusive; every returned ts must be earlier",
        "requests": request_count,
        "response_rows_received": response_rows,
        "rows_materialized_after_local_prefix": int(len(tail)),
        "response_rows_at_or_after_approved_end": out_of_bounds_rows,
        "duplicate_response_timestamps": 0,
        "unconfirmed_rows_inside_approved_tail": unconfirmed_rows,
        "raw_kline_rows_written_to_disk": 0,
        "first_tail_bar": tail["open_time"].iloc[0].isoformat() if not tail.empty else None,
        "last_tail_bar": tail["open_time"].iloc[-1].isoformat() if not tail.empty else None,
        "tail_sha256": sha256_bounded_frame(tail) if not tail.empty else None,
    }


def load_approved_bounded_frame(
    path: Path = DATA_PATH,
    *,
    end_exclusive: pd.Timestamp = REQUESTED_END,
    request_fn: Any = okx_request,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load the immutable local prefix and fill its bounded tail in memory."""

    end_exclusive = pd.Timestamp(end_exclusive)
    if end_exclusive.tzinfo is None:
        end_exclusive = end_exclusive.tz_localize("UTC")
    else:
        end_exclusive = end_exclusive.tz_convert("UTC")
    if end_exclusive != REQUESTED_END:
        raise RuntimeError(
            "holdout loader end_exclusive is frozen to 2026-08-21T00:00:00Z"
        )
    terminal_bar = end_exclusive - BAR_DURATION
    local, local_audit = _read_local_prefix_before(
        path,
        end_exclusive=end_exclusive,
    )
    local = local.sort_values("open_time")
    local_last = pd.to_datetime(local["open_time"], utc=True).iloc[-1]
    if local_last < terminal_bar:
        tail, api_audit = _fetch_approved_tail_memory(
            start_after=local_last,
            end_exclusive=end_exclusive,
            request_fn=request_fn,
        )
        raw = pd.concat([local, tail], ignore_index=True)
    elif local_last == terminal_bar:
        raw = local
        api_audit = {
            "api": OKX_HISTORY_API,
            "pagination_contract": "not called; local prefix already reached terminal bar",
            "requests": 0,
            "response_rows_received": 0,
            "rows_materialized_after_local_prefix": 0,
            "response_rows_at_or_after_approved_end": 0,
            "duplicate_response_timestamps": 0,
            "unconfirmed_rows_inside_approved_tail": 0,
            "raw_kline_rows_written_to_disk": 0,
            "first_tail_bar": None,
            "last_tail_bar": None,
            "tail_sha256": None,
        }
    else:
        raise RuntimeError("local prefix crossed the approved terminal bar")
    raw = raw.sort_values("open_time").reset_index(drop=True)
    times = pd.to_datetime(raw["open_time"], utc=True)
    numeric = raw[["open", "high", "low", "close", "volume"]].apply(
        pd.to_numeric, errors="coerce"
    )
    raw[numeric.columns] = numeric
    deltas = times.diff().dropna()
    quality = {
        "data_path": str(path),
        "data_source": "local immutable prefix + bounded OKX in-memory tail",
        "bounded_prefix_sha256": sha256_bounded_frame(raw),
        "hash_scope": "in-memory OHLCV rows through 2026-08-20 23:45 UTC only",
        "rows_read": int(len(raw)),
        "first_bar": times.iloc[0].isoformat(),
        "last_bar": times.iloc[-1].isoformat(),
        "approved_end_exclusive": end_exclusive.isoformat(),
        "terminal_bar_expected": terminal_bar.isoformat(),
        **local_audit,
        "api_tail": api_audit,
        "holdout_start": HOLDOUT_START.isoformat(),
        "holdout_rows_read": int((times >= HOLDOUT_START).sum()),
        "duplicate_timestamps": int(times.duplicated().sum()),
        "null_ohlcv_cells": int(numeric.isna().sum().sum()),
        "non_15m_gaps": int((deltas != BAR_DURATION).sum()),
        "nonpositive_ohlc_cells": int(
            (numeric[["open", "high", "low", "close"]] <= 0.0).sum().sum()
        ),
        "ohlc_body_violations": int(
            (numeric["high"] < numeric[["open", "close"]].max(axis=1)).sum()
            + (numeric["low"] > numeric[["open", "close"]].min(axis=1)).sum()
        ),
        "warmup_bars_available_before_requested_start": int(
            (times < REQUESTED_START).sum()
        ),
    }
    if times.iloc[-1] != terminal_bar:
        raise RuntimeError(f"bounded source does not reach approved terminal bar: {quality}")
    failures = {
        key: quality[key]
        for key in (
            "duplicate_timestamps",
            "null_ohlcv_cells",
            "non_15m_gaps",
            "nonpositive_ohlc_cells",
            "ohlc_body_violations",
        )
        if quality[key]
    }
    if failures:
        raise RuntimeError(f"approved bounded data quality failed: {failures}")
    if quality["warmup_bars_available_before_requested_start"] < MIN_WARMUP_BARS:
        raise RuntimeError(
            "approved source lacks the frozen causal warmup history: "
            f"{quality['warmup_bars_available_before_requested_start']} < {MIN_WARMUP_BARS}"
        )
    return raw, quality


def materialize_period_v12f_signals(
    frame: pd.DataFrame,
    period: Period,
) -> pd.DataFrame:
    """Materialize Pine's date-aware full-state W8 gate for one period.

    Columns used: ``open_time``, ``entry_allowed``, the causal ``v9_*`` raw
    signal columns and the causal ``ma6_w8_*_pass`` columns.  The date mask is
    based on the current bar's open and known 15-minute close; no future price
    or future feature is read.
    """

    out = frame.copy()
    times = pd.to_datetime(out["open_time"], utc=True)
    date_allowed = times.ge(period.start) & (times + BAR_DURATION).lt(period.end)
    gate_candidate = out["entry_allowed"].fillna(False).astype(bool) & date_allowed
    out["v12f_period_long"] = out["v9_long"].fillna(False).astype(bool) & (
        ~gate_candidate | out["ma6_w8_long_pass"].fillna(False).astype(bool)
    )
    out["v12f_period_short"] = out["v9_short"].fillna(False).astype(bool) & (
        ~gate_candidate | out["ma6_w8_short_pass"].fillna(False).astype(bool)
    )
    out["v12f_period_gate_candidate"] = gate_candidate
    return out


def _approved_signal_counts(
    frame: pd.DataFrame,
    arm: BacktestArm,
    period: Period,
) -> dict[str, int]:
    """Count only date-eligible guarded candidates under Pine time_close semantics."""

    times = pd.to_datetime(frame["open_time"], utc=True)
    active = times.ge(period.start) & (times + BAR_DURATION).lt(period.end)
    guarded = frame["entry_allowed"].fillna(False).astype(bool)
    raw_long = frame["v9_long"].fillna(False).astype(bool)
    raw_short = frame["v9_short"].fillna(False).astype(bool)
    raw_guarded = active & guarded & (raw_long | raw_short)
    if arm.name == "v12f_ma6_w8_full_gate":
        accepted = active & guarded & (
            (raw_long & frame["ma6_w8_long_pass"].fillna(False).astype(bool))
            | (raw_short & frame["ma6_w8_short_pass"].fillna(False).astype(bool))
        )
    else:
        accepted = raw_guarded
    return {
        "raw_guarded_candidates": int(raw_guarded.sum()),
        "entry_gate_pass_candidates": int(accepted.sum()),
        "entry_gate_rejected_candidates": int((raw_guarded & ~accepted).sum()),
    }


def _trade_anatomy(trades: pd.DataFrame) -> dict[str, Any]:
    if trades.empty:
        return {"trades": 0}
    unit = trades["project_net_return"].astype(float)
    winners = unit.loc[unit > 0.0]
    losers = unit.loc[unit <= 0.0]
    avg_win = float(winners.mean()) if not winners.empty else np.nan
    avg_loss = float(losers.mean()) if not losers.empty else np.nan
    return {
        "trades": int(len(trades)),
        "winner_count": int(len(winners)),
        "loser_count": int(len(losers)),
        "average_winner_net_bp": avg_win * 10_000.0,
        "average_loser_net_bp": avg_loss * 10_000.0,
        "payoff_ratio_average_win_to_loss": (
            avg_win / abs(avg_loss) if np.isfinite(avg_win) and avg_loss < 0.0 else np.nan
        ),
        "largest_winner_net_percent": float(unit.max() * 100.0),
        "largest_loser_net_percent": float(unit.min() * 100.0),
        "median_trade_net_bp": float(unit.median() * 10_000.0),
        "mean_holding_bars": float(trades["holding_bars"].mean()),
        "median_holding_bars": float(trades["holding_bars"].median()),
        "long": {
            "trades": int((trades["direction"] == "long").sum()),
            "mean_net_bp": float(
                trades.loc[trades["direction"] == "long", "project_net_return"].mean()
                * 10_000.0
            ),
        },
        "short": {
            "trades": int((trades["direction"] == "short").sum()),
            "mean_net_bp": float(
                trades.loc[trades["direction"] == "short", "project_net_return"].mean()
                * 10_000.0
            ),
        },
        "exit_reason_counts": {
            str(k): int(v) for k, v in trades["exit_reason"].value_counts().items()
        },
    }


def _monthly_returns(
    marked: pd.DataFrame,
    *,
    arm: str,
    period: Period,
) -> pd.DataFrame:
    if marked.empty:
        return pd.DataFrame()
    equity = (
        marked.sort_values("open_time")
        .drop_duplicates("open_time", keep="last")
        .set_index("open_time")["normalized_equity"]
    )
    month_end = equity.resample("ME").last().dropna()
    rows: list[dict[str, Any]] = []
    previous = 1.0
    for timestamp, value in month_end.items():
        rows.append(
            {
                "arm": arm,
                "period": period.name,
                "month": timestamp.strftime("%Y-%m"),
                "month_end_equity_multiple": float(value),
                "monthly_return_percent": float((value / previous - 1.0) * 100.0),
                "partial_first_or_last_month": bool(
                    timestamp.strftime("%Y-%m")
                    in {period.start.strftime("%Y-%m"), period.end.strftime("%Y-%m")}
                ),
            }
        )
        previous = float(value)
    return pd.DataFrame(rows)


def _continuous_protected_segment(
    equity: pd.DataFrame,
    trades: pd.DataFrame,
) -> list[dict[str, Any]]:
    """Slice May 4 onward from the continuous six-month state path.

    This retains any position, equity and cooldown state accumulated before
    the repository holdout. It is an operational diagnostic, not a substitute
    for the independent fresh-start holdout replay.
    """

    rows: list[dict[str, Any]] = []
    full_equity = equity.loc[equity["period"] == "requested_recent_6m"].copy()
    full_trades = trades.loc[trades["period"] == "requested_recent_6m"].copy()
    for arm, group in full_equity.groupby("variant", sort=False):
        group = group.sort_values("open_time")
        times = pd.to_datetime(group["open_time"], utc=True)
        prior = group.loc[times < HOLDOUT_START, "normalized_equity"]
        segment = group.loc[times >= HOLDOUT_START].copy()
        if prior.empty or segment.empty:
            raise RuntimeError(f"cannot form continuous protected segment for {arm}")
        start_equity = float(prior.iloc[-1])
        path = np.concatenate(
            [[start_equity], segment["normalized_equity"].to_numpy(dtype=float)]
        )
        arm_trades = full_trades.loc[full_trades["variant"] == arm].copy()
        entry_times = pd.to_datetime(arm_trades["entry_time"], utc=True)
        exit_times = pd.to_datetime(arm_trades["exit_time"], utc=True)
        rows.append(
            {
                "variant": arm,
                "period": "protected_holdout_continuous_state_diagnostic",
                "start": HOLDOUT_START.isoformat(),
                "end_exclusive": REQUESTED_END.isoformat(),
                "start_equity_multiple": start_equity,
                "end_equity_multiple": float(path[-1]),
                "return_percent": float((path[-1] / start_equity - 1.0) * 100.0),
                "max_drawdown_percent": float(max_drawdown(path) * 100.0),
                "entries_in_segment": int((entry_times >= HOLDOUT_START).sum()),
                "exits_in_segment": int((exit_times >= HOLDOUT_START).sum()),
                "carry_positions_crossing_holdout_start": int(
                    ((entry_times < HOLDOUT_START) & (exit_times >= HOLDOUT_START)).sum()
                ),
                "inference_note": (
                    "state-retaining equity diagnostic only; matched-control and ranking tests "
                    "belong to protected_holdout_fresh_start"
                ),
            }
        )
    return rows


def _anchor_controls(
    frame: pd.DataFrame,
    trades: pd.DataFrame,
    arm: BacktestArm,
    period: Period,
    *,
    seed: int,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    if trades.empty:
        return pd.DataFrame(), pd.DataFrame(), {
            "status": "not_applicable_no_trades",
            "control_net_bp_per_trade": np.nan,
            "candidate_minus_control_bp_per_trade": np.nan,
            "week_block_signflip_p_one_sided": np.nan,
        }
    controls = build_matched_controls(
        frame,
        trades,
        period,
        controls_per_trade=CONTROLS_PER_TRADE,
        seed=f"v12f-holdout1|{arm.name}|{period.name}|anchor",
        params=arm.params,
        take_profit_percent=None,
        take_profit_distance_basis="entry",
    )
    pairs = pair_controls(trades, controls)
    signflip = block_signflip(
        pairs,
        n_resamples=SIGNFLIP_RESAMPLES,
        seed=seed,
    )
    controls = controls.copy()
    pairs = pairs.copy()
    controls["arm"] = arm.name
    controls["period"] = period.name
    controls["assignment"] = "anchor"
    pairs["arm"] = arm.name
    pairs["period"] = period.name
    pairs["assignment"] = "anchor"
    return controls, pairs, {
        "status": "complete_exact_3_per_trade",
        "control_net_bp_per_trade": float(
            pairs["control_mean_project_net"].mean() * 10_000.0
        ),
        "candidate_minus_control_bp_per_trade": float(
            pairs["excess_return"].mean() * 10_000.0
        ),
        "week_block_signflip_p_one_sided": float(signflip["p_value"]),
        "week_blocks": int(signflip["n_blocks"]),
        "week_block_signflip": signflip,
    }


def _control_sensitivity(
    frame: pd.DataFrame,
    trades: pd.DataFrame,
    arm: BacktestArm,
    period: Period,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if trades.empty:
        return pd.DataFrame(), {"status": "not_applicable_no_trades"}
    for assignment_seed in range(CONTROL_ASSIGNMENT_SEEDS):
        controls = build_matched_controls(
            frame,
            trades,
            period,
            controls_per_trade=CONTROLS_PER_TRADE,
            seed=(
                f"v12f-holdout1|{arm.name}|{period.name}|"
                f"assignment-{assignment_seed:02d}"
            ),
            params=arm.params,
            take_profit_percent=None,
            take_profit_distance_basis="entry",
        )
        pairs = pair_controls(trades, controls)
        rows.append(
            {
                "arm": arm.name,
                "period": period.name,
                "assignment_seed": assignment_seed,
                "trades": int(len(trades)),
                "controls": int(len(controls)),
                "control_net_bp_per_trade": float(
                    pairs["control_mean_project_net"].mean() * 10_000.0
                ),
                "candidate_minus_control_bp_per_trade": float(
                    pairs["excess_return"].mean() * 10_000.0
                ),
                "selected_control_signal_indices_sha256": _canonical_sha256(
                    controls.sort_values(["trade_id", "control_rank"])[
                        "control_signal_i"
                    ].astype(int).tolist()
                ),
            }
        )
    table = pd.DataFrame(rows)
    excess = table["candidate_minus_control_bp_per_trade"].to_numpy(dtype=float)
    return table, {
        "status": "complete",
        "assignment_seeds": CONTROL_ASSIGNMENT_SEEDS,
        "median_excess_bp_per_trade": float(np.median(excess)),
        "p05_excess_bp_per_trade": float(np.quantile(excess, 0.05)),
        "p95_excess_bp_per_trade": float(np.quantile(excess, 0.95)),
        "positive_assignment_fraction": float((excess > 0.0).mean()),
    }


def run_arm_period(
    base_frame: pd.DataFrame,
    arm: BacktestArm,
    period: Period,
    *,
    seed: int,
) -> RunOutput:
    frame = materialize_period_v12f_signals(base_frame, period)
    trades, marked = simulate_symbol(
        frame,
        symbol="ETH_USDT_SWAP",
        arm=_standard_arm(arm.name),
        start=period.start,
        end=period.end,
        params=arm.params,
        round_trip_cost=ROUND_TRIP_COST,
        initial_capital=INITIAL_CAPITAL,
        execution=arm.execution,
        signal_columns=arm.signal_columns,
        entry_gate_columns=None,
    )
    if not trades.empty:
        trades = trades.copy()
        trades["variant"] = arm.name
        trades["period"] = period.name
        trades["trade_id"] = [
            f"{arm.name}|{period.name}|{int(row.signal_i)}|{int(row.entry_i)}|{row.direction}"
            for row in trades.itertuples(index=False)
        ]
    if not marked.empty:
        marked = marked.copy()
        marked["variant"] = arm.name
        marked["period"] = period.name
    summary = summarize(
        trades,
        marked,
        variant=arm.name,
        period=period.name,
        risk_percent=1.0,
    )
    summary.update(_approved_signal_counts(frame, arm, period))
    summary.update(_ranking_metrics(trades, seed=seed))
    controls, pairs, control_summary = _anchor_controls(
        frame,
        trades,
        arm,
        period,
        seed=seed + 1_000,
    )
    sensitivity, sensitivity_summary = _control_sensitivity(
        frame,
        trades,
        arm,
        period,
    )
    summary.update(
        {
            "change_contract": arm.change_contract,
            "strict_single_variable": arm.strict_single_variable,
            "pine_path": str(arm.pine_path.relative_to(PROJECT)),
            "pine_sha256": _sha256(arm.pine_path),
            "take_profit_percent": None,
            "atr_mult": arm.params.atr_mult,
            "max_sl_percent": arm.params.max_sl_percent,
            "intrabar_barrier_collisions": int(
                trades.get("intrabar_barrier_collision", pd.Series(dtype=bool)).sum()
            ),
            "trade_anatomy": _trade_anatomy(trades),
            "matched_control": control_summary,
            "control_assignment_sensitivity": sensitivity_summary,
        }
    )
    monthly = _monthly_returns(marked, arm=arm.name, period=period)
    return RunOutput(trades, marked, controls, pairs, sensitivity, monthly, summary)


def _concat(frames: list[pd.DataFrame]) -> pd.DataFrame:
    nonempty = [frame for frame in frames if not frame.empty]
    return pd.concat(nonempty, ignore_index=True) if nonempty else pd.DataFrame()


def _write_chart(equity: pd.DataFrame, monthly: pd.DataFrame) -> None:
    full_equity = equity.loc[equity["period"] == "requested_recent_6m"].copy()
    full_monthly = monthly.loc[monthly["period"] == "requested_recent_6m"].copy()
    fig, axes = plt.subplots(2, 1, figsize=(12, 9), constrained_layout=True)
    for arm, group in full_equity.groupby("variant", sort=False):
        axes[0].plot(
            pd.to_datetime(group["open_time"], utc=True),
            group["normalized_equity"],
            label=arm,
            linewidth=1.4,
        )
    axes[0].axvline(HOLDOUT_START, color="#9A3412", linestyle="--", linewidth=1.2)
    axes[0].set_title("Frozen V9 vs V12F — approved recent-six-month replay")
    axes[0].set_ylabel("Normalized marked equity")
    axes[0].grid(alpha=0.2)
    axes[0].legend()

    pivot = full_monthly.pivot(index="month", columns="arm", values="monthly_return_percent")
    pivot.plot.bar(ax=axes[1], color=["#64748B", "#2563EB"], width=0.8)
    axes[1].axhline(0.0, color="black", linewidth=0.8)
    axes[1].set_title("Monthly compounded return (February/August are partial)")
    axes[1].set_ylabel("Return (%)")
    axes[1].grid(axis="y", alpha=0.2)
    CHARTS.mkdir(parents=True, exist_ok=True)
    fig.savefig(CHART_OUTPUT, dpi=180)
    plt.close(fig)


def _output_hashes(paths: list[Path]) -> dict[str, str]:
    return {str(path.relative_to(PROJECT)): _sha256(path) for path in paths}


def _ledger_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(_json_safe(value), ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")


def _create_ledger_exclusive(value: dict[str, Any]) -> None:
    """Create the first-consumption lock atomically; two runners cannot race."""

    RESULTS.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        LEDGER_OUTPUT,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o644,
    )
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(_ledger_bytes(value))
        handle.flush()
        os.fsync(handle.fileno())


def _write_ledger(value: dict[str, Any]) -> None:
    """Atomically replace an already-created consumption ledger."""

    RESULTS.mkdir(parents=True, exist_ok=True)
    temporary = LEDGER_OUTPUT.with_suffix(f".json.tmp-{os.getpid()}")
    temporary.write_bytes(_ledger_bytes(value))
    os.replace(temporary, LEDGER_OUTPUT)


def validate_output_preflight() -> None:
    existing = [str(path.relative_to(PROJECT)) for path in MATERIAL_OUTPUTS if path.exists()]
    if existing:
        raise RuntimeError(
            "holdout output paths already exist; refusing to overwrite before consumption: "
            f"{existing}"
        )


def verify_existing() -> None:
    """Verify prior artifacts without reopening the approved OHLCV prefix."""

    validate_frozen_contract()
    validate_committed_runtime_provenance()
    ledger = json.loads(LEDGER_OUTPUT.read_text(encoding="utf-8"))
    if ledger.get("status") != "completed":
        raise RuntimeError(f"holdout ledger is not complete: {ledger.get('status')}")
    if ledger.get("approval") != OWNER_APPROVAL:
        raise RuntimeError("holdout ledger approval record drifted")
    if ledger.get("frozen_config_sha256") != FROZEN_CONFIG_SHA256:
        raise RuntimeError("holdout ledger frozen config hash drifted")
    expected_output_keys = {
        str(path.relative_to(PROJECT)) for path in MATERIAL_OUTPUTS
    }
    actual_output_keys = set(ledger.get("output_sha256", {}))
    if actual_output_keys != expected_output_keys:
        raise RuntimeError(
            "holdout ledger output set drifted: "
            f"expected={sorted(expected_output_keys)} actual={sorted(actual_output_keys)}"
        )
    failures: dict[str, dict[str, str]] = {}
    for relative, expected in ledger["output_sha256"].items():
        path = PROJECT / relative
        actual = _sha256(path) if path.is_file() else "MISSING"
        if actual != expected:
            failures[relative] = {"expected": expected, "actual": actual}
    if failures:
        raise RuntimeError(f"existing holdout artifacts failed verification: {failures}")
    print(f"verified {len(ledger['output_sha256'])} artifacts; holdout data not reopened")


def run_once() -> dict[str, Any]:
    """Perform the single approved access and persist an append-only ledger."""

    validate_frozen_contract()
    validate_committed_runtime_provenance()
    if LEDGER_OUTPUT.exists():
        raise RuntimeError(
            "consumption #1 ledger already exists; use --verify-existing instead of rereading holdout"
        )
    validate_output_preflight()
    contract = frozen_config_contract()
    config_hash = _canonical_sha256(contract)
    start_ledger = {
        "artifact": "V12F holdout consumption ledger",
        "status": "started",
        "approval": OWNER_APPROVAL,
        "frozen_config_sha256": config_hash,
        "started_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "holdout_rows_opened": "pending",
        "warning": "A failed data read still counts as consumption attempt #1.",
    }
    _create_ledger_exclusive(start_ledger)
    try:
        raw, quality = load_approved_bounded_frame()
        frame = build_v12_feature_frame(raw)
        outputs: list[RunOutput] = []
        result_rows: list[dict[str, Any]] = []
        for arm_index, arm in enumerate(approved_arms()):
            for period_index, period in enumerate(PERIODS):
                result = run_arm_period(
                    frame,
                    arm,
                    period,
                    seed=20_260_821 + arm_index * 100 + period_index,
                )
                outputs.append(result)
                result_rows.append(result.summary)

        by_key = {(row["variant"], row["period"]): row for row in result_rows}
        for row in result_rows:
            baseline = by_key[("v9_frozen_baseline", row["period"])]
            row["return_delta_vs_v9_percentage_points"] = float(
                row["return_percent"] - baseline["return_percent"]
            )
            row["drawdown_delta_vs_v9_percentage_points"] = float(
                row["max_drawdown_15m_percent"]
                - baseline["max_drawdown_15m_percent"]
            )
            row["net_bp_delta_vs_v9_per_trade"] = float(
                row["project_net_bp_per_trade"]
                - baseline["project_net_bp_per_trade"]
            )

        trades = _concat([output.trades for output in outputs])
        equity = _concat([output.equity for output in outputs])
        controls = _concat([output.controls for output in outputs])
        pairs = _concat([output.pairs for output in outputs])
        sensitivity = _concat([output.sensitivity for output in outputs])
        monthly = _concat([output.monthly for output in outputs])
        continuous_protected = _continuous_protected_segment(equity, trades)

        payload = {
            "artifact": "ETH 15m V12F owner-approved holdout consumption #1",
            "status": "completed research replay; paper-only",
            "approval": OWNER_APPROVAL,
            "frozen_config": contract,
            "frozen_config_sha256": config_hash,
            "data_quality": quality,
            "code_provenance": {
                "git_commit_at_run": current_commit(),
                "runtime_paths_committed_and_clean": True,
                "unrelated_worktree_changes_may_exist": True,
                "runner_sha256": _sha256(Path(__file__)),
                "execution_engine_sha256": _sha256(
                    PROJECT / "yoyo/layers/l3_backtest/pine_allin_v7.py"
                ),
                "cross_feature_sha256": _sha256(
                    PROJECT / "yoyo/layers/l2_judgment/pine_cross_features.py"
                ),
            },
            "results": result_rows,
            "continuous_protected_segment": continuous_protected,
            "result_keys": [
                "requested_recent_6m is the user's full window",
                (
                    "protected_holdout_fresh_start is the independent repository "
                    "holdout verdict with state reset"
                ),
                (
                    "protected_holdout_continuous_state_diagnostic retains the six-month "
                    "path's pre-May-4 position/equity/cooldown state"
                ),
            ],
            "selection_after_holdout": False,
            "parameter_search_on_holdout": False,
            "v12e_evaluated": False,
            "tbsl_evaluated": False,
            "model_trained_or_scored": False,
            "official_tradingview_parity_passed": False,
            "training_eligible": False,
            "forward_eligible": False,
            "production_eligible": False,
        }

        RESULTS.mkdir(parents=True, exist_ok=True)
        CONFIG_OUTPUT.write_text(
            json.dumps(_json_safe(contract), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        TRADES_OUTPUT.write_text(trades.to_csv(index=False), encoding="utf-8")
        CONTROLS_OUTPUT.write_text(controls.to_csv(index=False), encoding="utf-8")
        PAIRS_OUTPUT.write_text(pairs.to_csv(index=False), encoding="utf-8")
        SENSITIVITY_OUTPUT.write_text(sensitivity.to_csv(index=False), encoding="utf-8")
        MONTHLY_OUTPUT.write_text(monthly.to_csv(index=False), encoding="utf-8")
        EQUITY_OUTPUT.write_text(equity.to_csv(index=False), encoding="utf-8")
        SUMMARY_OUTPUT.write_text(
            json.dumps(_json_safe(payload), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _write_chart(equity, monthly)

        hash_paths = [
            CONFIG_OUTPUT,
            SUMMARY_OUTPUT,
            TRADES_OUTPUT,
            CONTROLS_OUTPUT,
            PAIRS_OUTPUT,
            SENSITIVITY_OUTPUT,
            MONTHLY_OUTPUT,
            EQUITY_OUTPUT,
            CHART_OUTPUT,
        ]
        completed_ledger = {
            **start_ledger,
            "status": "completed",
            "completed_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
            "holdout_rows_opened": int(quality["holdout_rows_read"]),
            "bounded_prefix_sha256": quality["bounded_prefix_sha256"],
            "output_sha256": _output_hashes(hash_paths),
            "result_identity_sha256": _canonical_sha256(
                _json_safe(
                    {
                        "fresh_start_results": result_rows,
                        "continuous_protected_segment": continuous_protected,
                    }
                )
            ),
            "rerun_policy": "do not reopen data; use --verify-existing",
        }
        _write_ledger(completed_ledger)

        compact = pd.DataFrame(result_rows)[
            [
                "variant",
                "period",
                "trades",
                "return_percent",
                "max_drawdown_15m_percent",
                "win_rate",
                "monetary_profit_factor",
                "project_net_bp_per_trade",
                "return_delta_vs_v9_percentage_points",
            ]
        ]
        print(compact.to_string(index=False))
        print(f"\nsummary={SUMMARY_OUTPUT}")
        print(f"ledger={LEDGER_OUTPUT}")
        print(f"holdout_rows_read={quality['holdout_rows_read']}")
        return payload
    except Exception as exc:
        failed = {
            **start_ledger,
            "status": "failed_after_consumption_started",
            "failed_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "rerun_requires_owner_decision": True,
        }
        _write_ledger(failed)
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--verify-existing",
        action="store_true",
        help="verify artifact hashes without reopening holdout data",
    )
    args = parser.parse_args()
    if args.verify_existing:
        verify_existing()
    else:
        run_once()


if __name__ == "__main__":
    main()
