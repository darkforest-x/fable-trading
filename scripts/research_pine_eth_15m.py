#!/usr/bin/env python3
"""Build the bounded ETH perpetual 15m Pine research experiment.

At decision bar ``t`` every signal and feature in this builder uses only
``open/high/low/close/volume`` through ``t``.  Pine features use SMA(hl2,
10/40/60), EMA(close, 100), Pine/Wilder ATR14, a trailing 200-bar percentile,
10-bar change and HMA10.  The one project judgment feature used by V9 is
``slow_slope_12 = EMA200 / EMA200.shift(12) - 1``.  The exported L2 research
rows contain the 28 causal columns documented in
``yoyo.layers.l2_judgment.features``.  Entry is at ``open[t+1]``; future bars
are consulted only by the exit replay and outcome columns.

The loader stops at 2026-03-01 UTC and refuses to approach the repository
holdout at 2026-05-04.  This script never trains a model, discovers an ACTIVE
artifact, promotes, deploys, writes forward logs, or touches a live account.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import warnings
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from pandas.errors import PerformanceWarning

warnings.filterwarnings("ignore", category=PerformanceWarning)

from yoyo.data.indicators import add_indicators as add_project_indicators
from yoyo.evaluation.permutation import permutation_test
from yoyo.layers.l2_judgment.features import (
    FEATURE_COLUMNS,
    add_features,
    extract_feature_rows_for_side,
)
from yoyo.layers.l3_backtest.pine_allin_v7 import (
    Arm,
    ExecutionParameters,
    SignalParameters,
    add_indicators as add_pine_indicators,
    auc_from_scores,
    deterministic_control_indices,
    load_development_frame,
    max_drawdown,
    profit_factor,
    simulate_symbol,
)


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
CONFIG_PATH = EXPERIMENT / "config.json"
RESULTS = EXPERIMENT / "results"
CHARTS = RESULTS / "charts"
INITIAL_CAPITAL = 500.0
N_RESAMPLES = 10_000


@dataclass(frozen=True)
class Period:
    name: str
    start: pd.Timestamp
    end: pd.Timestamp


@dataclass(frozen=True)
class Variant:
    name: str
    long_column: str
    short_column: str
    score_column: str = "v9_score"
    skip_logic: bool = True
    break_even: bool = True
    trailing: bool = False
    opposite_signal_action: str = "reverse"
    params: SignalParameters = SignalParameters()
    final_evaluated: bool = False
    selection_status: str = "development_ablation"


SPLITS = (
    Period("discovery_2023", pd.Timestamp("2023-01-01", tz="UTC"), pd.Timestamp("2024-01-01", tz="UTC")),
    Period("confirmation_2024", pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2025-01-01", tz="UTC")),
    Period(
        "final_preholdout_2025_202602",
        pd.Timestamp("2025-01-01", tz="UTC"),
        pd.Timestamp("2026-03-01", tz="UTC"),
    ),
)

DEVELOPMENT_BLOCKS = (
    Period("2023H1", pd.Timestamp("2023-01-01", tz="UTC"), pd.Timestamp("2023-07-01", tz="UTC")),
    Period("2023H2", pd.Timestamp("2023-07-01", tz="UTC"), pd.Timestamp("2024-01-01", tz="UTC")),
    Period("2024H1", pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2024-07-01", tz="UTC")),
    Period("2024H2", pd.Timestamp("2024-07-01", tz="UTC"), pd.Timestamp("2025-01-01", tz="UTC")),
)


def _utc(value: str) -> pd.Timestamp:
    result = pd.Timestamp(value)
    return result.tz_localize("UTC") if result.tzinfo is None else result.tz_convert("UTC")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def current_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "UNAVAILABLE"


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    if config["eligibility"]["holdout_consumed"]:
        raise RuntimeError("this research builder must never run after holdout consumption")
    if config["instrument"]["bar_minutes"] != 15:
        raise ValueError("ETH Pine research contract must remain 15 minutes")
    return config


def exact_execution(*, equity_frequency: str | None = None) -> ExecutionParameters:
    """Return the frozen V9 accounting semantics without changing barriers."""

    return ExecutionParameters(
        stop_distance_basis="signal_close",
        sizing_price_basis="signal_close",
        tick_size=0.01,
        commission_per_side=0.001,
        skip_return_basis="net",
        force_close_at_end=True,
        equity_frequency=equity_frequency,
    )


def load_research_frame(config: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    data_path = PROJECT / config["instrument"]["data_path"]
    safe_end = _utc(config["time_contract"]["safe_end_exclusive"])
    holdout_start = _utc(config["time_contract"]["holdout_start"])
    raw = load_development_frame(data_path, safe_end=safe_end, holdout_start=holdout_start)
    times = pd.to_datetime(raw["open_time"], utc=True)
    deltas = times.diff().dropna()
    numeric = raw[["open", "high", "low", "close", "volume"]].apply(
        pd.to_numeric, errors="coerce"
    )
    quality = {
        "data_path": str(data_path),
        "sha256": sha256_file(data_path),
        "rows_read": int(len(raw)),
        "first_bar": times.iloc[0].isoformat(),
        "last_bar": times.iloc[-1].isoformat(),
        "safe_end_exclusive": safe_end.isoformat(),
        "holdout_start": holdout_start.isoformat(),
        "holdout_rows_read": int((times >= holdout_start).sum()),
        "duplicate_timestamps": int(times.duplicated().sum()),
        "null_ohlcv_cells": int(numeric.isna().sum().sum()),
        "non_15m_gaps": int((deltas != pd.Timedelta(minutes=15)).sum()),
        "ohlc_body_violations": int(
            (numeric["high"] < numeric[["open", "close"]].max(axis=1)).sum()
            + (numeric["low"] > numeric[["open", "close"]].min(axis=1)).sum()
        ),
    }
    if any(
        quality[key]
        for key in (
            "holdout_rows_read",
            "duplicate_timestamps",
            "null_ohlcv_cells",
            "non_15m_gaps",
            "ohlc_body_violations",
        )
    ):
        raise RuntimeError(f"data quality contract failed: {quality}")
    return raw, quality


def build_feature_frame(raw: pd.DataFrame) -> pd.DataFrame:
    """Join the Pine translation and the project's causal judgment features."""

    params = SignalParameters()
    pine = add_pine_indicators(raw, params)
    project = add_features(add_project_indicators(raw))
    out = pine.copy()
    for column in project.columns:
        if column not in out.columns:
            out[column] = project[column].to_numpy()

    source = (out["high"] + out["low"]) / 2.0
    basis = source.rolling(params.osc_basis_len, min_periods=params.osc_basis_len).mean()
    difference = source - basis
    out["osc_percentile99"] = difference.rolling(
        params.osc_percentile_len, min_periods=params.osc_percentile_len
    ).quantile(params.osc_percentile / 100.0, interpolation="linear")
    out["osc_percentile_safe"] = out["osc_percentile99"].gt(0.0)
    out["v9_score"] = out["osc"].abs().fillna(0.0)

    fast = out["fast_ma"]
    slow = out["slow_ma"]
    cross_up = (fast > slow) & (fast.shift(1) <= slow.shift(1))
    cross_down = (fast < slow) & (fast.shift(1) >= slow.shift(1))

    def add_signal(name: str, *, threshold: float, slope_lag: int | None) -> None:
        long_filter = (out["close"] > slow) & (out["close"] > out["regime_ma"])
        short_filter = (out["close"] < slow) & (out["close"] < out["regime_ma"])
        if slope_lag is not None:
            slope = out["ema200"] / out["ema200"].shift(slope_lag) - 1.0
            out[f"slow_slope_{slope_lag}"] = slope
            long_filter &= slope.gt(0.0)
            short_filter &= slope.lt(0.0)
        out[f"{name}_long"] = (
            out["osc_percentile_safe"]
            & cross_up
            & long_filter
            & out["osc"].gt(threshold)
            & out["osc"].gt(out["osc"].shift(1))
        ).fillna(False)
        out[f"{name}_short"] = (
            out["osc_percentile_safe"]
            & cross_down
            & short_filter
            & out["osc"].lt(-threshold)
            & out["osc"].lt(out["osc"].shift(1))
        ).fillna(False)

    add_signal("v8", threshold=0.2, slope_lag=None)
    add_signal("slope12_osc02", threshold=0.2, slope_lag=12)
    add_signal("slope72_osc02", threshold=0.2, slope_lag=72)
    add_signal("v9", threshold=0.1, slope_lag=12)

    out["v9_density_long"] = out["v9_long"] & out["ma_spread_pct"].le(0.0028)
    out["v9_density_short"] = out["v9_short"] & out["ma_spread_pct"].le(0.0028)
    out["v9_confirm_long"] = out["v9_long"].shift(1, fill_value=False)
    out["v9_confirm_short"] = out["v9_short"].shift(1, fill_value=False)
    out["v9_confirm_score"] = out["v9_score"].shift(1).fillna(0.0)
    out["v10_volume_long"] = out["v9_long"] & out["vol_ratio_mean8"].ge(1.0)
    out["v10_volume_short"] = out["v9_short"] & out["vol_ratio_mean8"].ge(1.0)
    return out


def variants() -> tuple[Variant, ...]:
    return (
        Variant(
            "v8_eth_baseline",
            "v8_long",
            "v8_short",
            final_evaluated=True,
            selection_status="rejected_absolute_net",
        ),
        Variant(
            "v8_plus_slope12",
            "slope12_osc02_long",
            "slope12_osc02_short",
            final_evaluated=True,
            selection_status="intermediate",
        ),
        Variant(
            "v8_slope72",
            "slope72_osc02_long",
            "slope72_osc02_short",
            final_evaluated=True,
            selection_status="rejected_final_sign_flip",
        ),
        Variant(
            "v9_locked",
            "v9_long",
            "v9_short",
            final_evaluated=True,
            selection_status="research_candidate_not_production",
        ),
        Variant("v9_density_gate", "v9_density_long", "v9_density_short"),
        Variant(
            "v9_confirm_1bar",
            "v9_confirm_long",
            "v9_confirm_short",
            score_column="v9_confirm_score",
        ),
        Variant(
            "v9_no_cooldown",
            "v9_long",
            "v9_short",
            skip_logic=False,
        ),
        Variant(
            "v9_no_break_even",
            "v9_long",
            "v9_short",
            break_even=False,
        ),
        Variant(
            "v9_trailing_default",
            "v9_long",
            "v9_short",
            trailing=True,
        ),
        Variant(
            "v9_close_only",
            "v9_long",
            "v9_short",
            opposite_signal_action="close_only",
        ),
        Variant(
            "v10_volume_hypothesis",
            "v10_volume_long",
            "v10_volume_short",
            final_evaluated=True,
            selection_status="post_final_selection_forward_hypothesis",
        ),
    )


def _arm(spec: Variant, risk_percent: float) -> Arm:
    return Arm(
        name=spec.name,
        signal_kind="v7",
        sizing_kind="risk",
        risk_per_trade_percent=float(risk_percent),
        max_leverage=13.0,
        time_boosts=False,
        skip_logic=spec.skip_logic,
        use_break_even=spec.break_even,
        use_trailing_stop=spec.trailing,
        opposite_signal_action=spec.opposite_signal_action,
    )


def simulate_period(
    frame: pd.DataFrame,
    spec: Variant,
    period: Period,
    *,
    risk_percent: float = 1.0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    trades, marked = simulate_symbol(
        frame,
        symbol="ETH_USDT_SWAP",
        arm=_arm(spec, risk_percent),
        start=period.start,
        end=period.end,
        params=spec.params,
        round_trip_cost=0.002,
        initial_capital=INITIAL_CAPITAL,
        execution=exact_execution(equity_frequency=None),
        signal_columns=(spec.long_column, spec.short_column, spec.score_column),
    )
    if not trades.empty:
        trades = trades.copy()
        trades["variant"] = spec.name
        trades["split"] = period.name
        trades["trade_id"] = [
            f"{spec.name}|{period.name}|{int(row.signal_i)}|{int(row.entry_i)}|{row.direction}"
            for row in trades.itertuples(index=False)
        ]
    if not marked.empty:
        marked = marked.copy()
        marked["variant"] = spec.name
        marked["split"] = period.name
        marked["risk_percent"] = risk_percent
    return trades, marked


def summarize(
    trades: pd.DataFrame,
    marked: pd.DataFrame,
    *,
    variant: str,
    period: str,
    risk_percent: float,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "variant": variant,
        "period": period,
        "risk_percent": risk_percent,
        "trades": int(len(trades)),
    }
    if trades.empty:
        row.update(
            {
                "gross_bp_per_trade": np.nan,
                "project_net_bp_per_trade": np.nan,
                "pine_net_bp_per_trade": np.nan,
                "win_rate": np.nan,
                "monetary_profit_factor": np.nan,
                "unit_profit_factor": np.nan,
                "return_percent": 0.0,
                "max_drawdown_15m_percent": np.nan,
                "max_drawdown_daily_percent": np.nan,
            }
        )
        return row

    bar_equity = (
        marked.sort_values("open_time")
        .drop_duplicates("open_time", keep="last")
        .set_index("open_time")["normalized_equity"]
    )
    daily = bar_equity.resample("1D").last().ffill()
    unit = trades["project_net_return"].to_numpy(dtype=float)
    row.update(
        {
            "gross_bp_per_trade": float(trades["gross_return"].mean() * 10_000.0),
            "project_net_bp_per_trade": float(unit.mean() * 10_000.0),
            "pine_net_bp_per_trade": float(trades["net_return"].mean() * 10_000.0),
            "win_rate": float((unit > 0.0).mean()),
            "monetary_profit_factor": float(profit_factor(trades["pnl"])),
            "unit_profit_factor": float(profit_factor(unit)),
            "return_percent": float((trades["exit_equity"].iloc[-1] / INITIAL_CAPITAL - 1.0) * 100.0),
            "max_drawdown_15m_percent": float(max_drawdown(bar_equity) * 100.0),
            "max_drawdown_daily_percent": float(max_drawdown(daily) * 100.0),
            "median_holding_bars": float(trades["holding_bars"].median()),
            "mean_leverage": float(trades["leverage"].mean()),
            "max_leverage": float(trades["leverage"].max()),
            "long_trades": int((trades["direction"] == "long").sum()),
            "short_trades": int((trades["direction"] == "short").sum()),
            "period_end_exits": int((trades["exit_reason"] == "period_end").sum()),
        }
    )
    return row


def _period_mask(frame: pd.DataFrame, period: Period) -> np.ndarray:
    times = pd.to_datetime(frame["open_time"], utc=True)
    return ((times >= period.start) & (times < period.end)).to_numpy()


def _atr_month_buckets(frame: pd.DataFrame, period: Period, buckets: int = 5) -> np.ndarray:
    times = pd.to_datetime(frame["open_time"], utc=True)
    mask = _period_mask(frame, period)
    result = np.full(len(frame), -1, dtype=int)
    eligible = np.flatnonzero(mask & frame["entry_allowed"].fillna(False).to_numpy())
    helper = pd.DataFrame(
        {
            "i": eligible,
            "month": times.iloc[eligible].dt.strftime("%Y-%m").to_numpy(),
            "atr": frame["atr"].iloc[eligible].to_numpy(dtype=float),
        }
    ).dropna()
    for _, group in helper.groupby("month", sort=False):
        indices = group["i"].to_numpy(dtype=int)
        if len(group) < buckets:
            result[indices] = 0
        else:
            ranks = group["atr"].rank(method="first")
            result[indices] = pd.qcut(ranks, q=buckets, labels=False).to_numpy(dtype=int)
    return result


def control_outcome(
    frame: pd.DataFrame,
    *,
    signal_i: int,
    direction: int,
    holding_bars: int,
    params: SignalParameters,
) -> dict[str, Any]:
    """Replay a matched entry under the exact V9 stop/BE/cost contract."""

    entry_i = signal_i + 1
    horizon_exit_i = entry_i + int(holding_bars)
    open_ = frame["open"].to_numpy(dtype=float)
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    entry_price = float(open_[entry_i])
    signal_close = float(close[signal_i])
    distance = min(
        float(frame["atr"].iloc[signal_i]) * params.atr_mult,
        signal_close * params.max_sl_percent / 100.0,
    )
    distance = max(1, int(round(distance / 0.01))) * 0.01
    stop = entry_price - direction * distance
    exit_i = horizon_exit_i
    exit_price = float(close[exit_i])
    reason = "copied_horizon"
    for i in range(entry_i, horizon_exit_i + 1):
        if direction > 0 and low[i] <= stop:
            exit_i = i
            exit_price = min(float(open_[i]), stop)
            reason = "stop"
            break
        if direction < 0 and high[i] >= stop:
            exit_i = i
            exit_price = max(float(open_[i]), stop)
            reason = "stop"
            break
        if direction > 0 and high[i] >= entry_price * (
            1.0 + params.break_even_trigger_percent / 100.0
        ):
            stop = max(stop, entry_price * (1.0 + params.break_even_offset_percent / 100.0))
        elif direction < 0 and low[i] <= entry_price * (
            1.0 - params.break_even_trigger_percent / 100.0
        ):
            stop = min(stop, entry_price * (1.0 - params.break_even_offset_percent / 100.0))
    ratio = exit_price / entry_price
    gross = direction * (ratio - 1.0)
    commission = 0.001 * (1.0 + ratio)
    return {
        "control_entry_i": entry_i,
        "control_exit_i": exit_i,
        "control_entry_price": entry_price,
        "control_exit_price": exit_price,
        "control_exit_reason": reason,
        "control_gross_return": gross,
        "control_project_net_return": gross - 0.002,
        "control_pine_net_return": gross - commission,
    }


def build_matched_controls(
    frame: pd.DataFrame,
    trades: pd.DataFrame,
    period: Period,
    *,
    controls_per_trade: int = 3,
    seed: str = "pine-eth15m-v9-controls-v1",
) -> pd.DataFrame:
    """Build non-reused, split-contained controls from exact causal strata."""

    if trades.empty:
        return pd.DataFrame()
    times = pd.to_datetime(frame["open_time"], utc=True)
    mask = _period_mask(frame, period)
    active = np.flatnonzero(mask)
    first_i, last_i = int(active[0]), int(active[-1])
    atr_bucket = _atr_month_buckets(frame, period)
    hk = times.dt.tz_convert("Asia/Hong_Kong")
    month = times.dt.strftime("%Y-%m").to_numpy()
    block = (hk.dt.hour.to_numpy(dtype=int) // 6).astype(int)
    all_crosses = frame["cross_long"].to_numpy(dtype=bool) | frame["cross_short"].to_numpy(dtype=bool)
    base_pool = (
        mask
        & frame["entry_allowed"].fillna(False).to_numpy(dtype=bool)
        & ~all_crosses
        & (atr_bucket >= 0)
    )

    pool_by_stratum: dict[tuple[str, int, int], list[int]] = {}
    for i in np.flatnonzero(base_pool):
        key = (str(month[i]), int(block[i]), int(atr_bucket[i]))
        pool_by_stratum.setdefault(key, []).append(int(i))

    used: set[int] = set()
    rows: list[dict[str, Any]] = []
    for trade in trades.sort_values(["entry_time", "trade_id"]).itertuples(index=False):
        signal_i = int(trade.signal_i)
        horizon = int(trade.holding_bars)
        key = (str(month[signal_i]), int(block[signal_i]), int(atr_bucket[signal_i]))
        candidates = []
        for candidate_i in pool_by_stratum.get(key, []):
            candidate_end = candidate_i + 1 + horizon
            if candidate_i in used or candidate_i < first_i or candidate_end > last_i:
                continue
            candidates.append(candidate_i)
        chosen = deterministic_control_indices(
            str(trade.trade_id), candidates, n=controls_per_trade, seed=seed
        )
        direction = 1 if str(trade.direction) == "long" else -1
        for rank, control_i in enumerate(chosen):
            used.add(control_i)
            outcome = control_outcome(
                frame,
                signal_i=control_i,
                direction=direction,
                holding_bars=horizon,
                params=SignalParameters(osc_threshold=0.1),
            )
            rows.append(
                {
                    "trade_id": str(trade.trade_id),
                    "candidate_signal_i": signal_i,
                    "candidate_entry_time": trade.entry_time,
                    "candidate_project_net_return": float(trade.project_net_return),
                    "direction": str(trade.direction),
                    "holding_bars": horizon,
                    "control_rank": rank,
                    "control_signal_i": control_i,
                    "control_signal_time": times.iloc[control_i],
                    "stratum_month": key[0],
                    "stratum_hk_6h": key[1],
                    "stratum_atr_quintile": key[2],
                    **outcome,
                }
            )
    controls = pd.DataFrame(rows)
    if controls.empty:
        raise RuntimeError("matched-control construction produced no rows")
    counts = controls.groupby("trade_id")["control_signal_i"].size()
    missing = sorted(set(trades["trade_id"]) - set(counts.index))
    underfilled = counts.loc[counts < controls_per_trade].to_dict()
    if missing or underfilled:
        raise RuntimeError(
            "matched-control construction must fail closed when an exact stratum is "
            f"underfilled; missing={missing[:5]}, underfilled={dict(list(underfilled.items())[:5])}"
        )
    return controls


def block_signflip(
    pairs: pd.DataFrame,
    *,
    n_resamples: int = N_RESAMPLES,
    seed: int = 20260821,
) -> dict[str, Any]:
    """One-sided UTC-week sign-flip on candidate-minus-control excess."""

    if pairs.empty:
        return {"p_value": np.nan, "n_blocks": 0}
    data = pairs.copy()
    data["week"] = (
        pd.to_datetime(data["candidate_entry_time"], utc=True)
        .dt.tz_localize(None)
        .dt.to_period("W-SUN")
        .astype(str)
    )
    values = data.groupby("week", sort=True)["excess_return"].mean().to_numpy(dtype=float)
    observed = float(values.mean())
    rng = np.random.default_rng(seed)
    signs = rng.choice(np.array([-1.0, 1.0]), size=(n_resamples, len(values)))
    null = (signs * values).mean(axis=1)
    p_value = float((np.sum(null >= observed) + 1) / (n_resamples + 1))
    return {
        "statistic_mean_excess_bp": observed * 10_000.0,
        "p_value": p_value,
        "n_blocks": int(len(values)),
        "n_resamples": int(n_resamples),
        "null_mean_bp": float(null.mean() * 10_000.0),
        "null_std_bp": float(null.std() * 10_000.0),
    }


def week_bootstrap_ci(
    values: pd.DataFrame,
    value_column: str,
    *,
    n_resamples: int = N_RESAMPLES,
    seed: int = 20260822,
) -> dict[str, float]:
    """Equal-week nonparametric bootstrap CI for a time-clustered mean."""

    data = values.copy()
    data["week"] = (
        pd.to_datetime(data["candidate_entry_time"], utc=True)
        .dt.tz_localize(None)
        .dt.to_period("W-SUN")
        .astype(str)
    )
    weekly = data.groupby("week", sort=True)[value_column].mean().to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    draws = rng.choice(weekly, size=(n_resamples, len(weekly)), replace=True).mean(axis=1)
    return {
        "mean_bp": float(weekly.mean() * 10_000.0),
        "ci95_low_bp": float(np.quantile(draws, 0.025) * 10_000.0),
        "ci95_high_bp": float(np.quantile(draws, 0.975) * 10_000.0),
        "n_weeks": int(len(weekly)),
    }


def pair_controls(trades: pd.DataFrame, controls: pd.DataFrame) -> pd.DataFrame:
    grouped = controls.groupby("trade_id", sort=False).agg(
        control_count=("control_signal_i", "size"),
        control_mean_project_net=("control_project_net_return", "mean"),
    )
    pairs = trades[
        ["trade_id", "entry_time", "direction", "project_net_return", "score"]
    ].merge(grouped, left_on="trade_id", right_index=True, how="inner")
    pairs = pairs.rename(columns={"entry_time": "candidate_entry_time"})
    pairs["excess_return"] = (
        pairs["project_net_return"] - pairs["control_mean_project_net"]
    )
    return pairs


def concentration_diagnostics(trades: pd.DataFrame) -> dict[str, Any]:
    """Quantify whether a positive mean depends on a few long-tail trades."""

    values = trades["project_net_return"].sort_values(ascending=False).to_numpy(dtype=float)
    positives = values[values > 0.0]
    total = float(values.sum())
    positive_total = float(positives.sum())
    monthly = trades.assign(
        month=(
            pd.to_datetime(trades["entry_time"], utc=True)
            .dt.tz_localize(None)
            .dt.to_period("M")
            .astype(str)
        )
    ).groupby("month")["project_net_return"].mean()

    def share_top(k: int, denominator: float) -> float:
        return float(values[:k].sum() / denominator) if denominator != 0.0 else np.nan

    reverse = trades.loc[trades["exit_reason"] == "reverse", "project_net_return"]
    stops = trades.loc[trades["exit_reason"] == "stop", "project_net_return"]
    return {
        "trades": int(len(values)),
        "positive_trades": int((values > 0.0).sum()),
        "positive_trade_fraction": float((values > 0.0).mean()),
        "total_unit_net_bp": total * 10_000.0,
        "top1_share_of_net": share_top(1, total),
        "top3_share_of_net": share_top(3, total),
        "top5_share_of_net": share_top(5, total),
        "top1_share_of_positive_sum": share_top(1, positive_total),
        "top3_share_of_positive_sum": share_top(3, positive_total),
        "mean_without_top1_bp": float(values[1:].mean() * 10_000.0) if len(values) > 1 else np.nan,
        "mean_without_top3_bp": float(values[3:].mean() * 10_000.0) if len(values) > 3 else np.nan,
        "reverse_trades": int(len(reverse)),
        "reverse_total_unit_net_bp": float(reverse.sum() * 10_000.0),
        "stop_trades": int(len(stops)),
        "stop_total_unit_net_bp": float(stops.sum() * 10_000.0),
        "positive_months": int((monthly > 0.0).sum()),
        "months_with_trades": int(len(monthly)),
        "median_monthly_net_bp": float(monthly.median() * 10_000.0),
    }


def attach_l2_features(featured: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    """Export causal Pine-signal feature rows; never fit or score a model."""

    pieces: list[pd.DataFrame] = []
    for side in ("long", "short"):
        selected = trades.loc[trades["direction"] == side].copy()
        if selected.empty:
            continue
        indices = selected["signal_i"].astype(int).tolist()
        features = extract_feature_rows_for_side(featured, indices, side)
        features.insert(0, "side", side)
        features.insert(0, "signal_time", selected["signal_time"].to_numpy())
        features.insert(0, "trade_id", selected["trade_id"].to_numpy())
        features["label_end"] = selected["exit_time"].to_numpy()
        features["project_net_return"] = selected["project_net_return"].to_numpy()
        features["training_eligible"] = False
        pieces.append(features)
    return pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()


def threshold_search(frame: pd.DataFrame, thresholds: Iterable[float]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    fast = frame["fast_ma"]
    slow = frame["slow_ma"]
    cross_up = (fast > slow) & (fast.shift(1) <= slow.shift(1))
    cross_down = (fast < slow) & (fast.shift(1) >= slow.shift(1))
    long_base = (
        frame["osc_percentile_safe"]
        & cross_up
        & (frame["close"] > slow)
        & (frame["close"] > frame["regime_ma"])
        & frame["slow_slope_12"].gt(0.0)
    )
    short_base = (
        frame["osc_percentile_safe"]
        & cross_down
        & (frame["close"] < slow)
        & (frame["close"] < frame["regime_ma"])
        & frame["slow_slope_12"].lt(0.0)
    )
    for threshold in thresholds:
        tag = str(threshold).replace(".", "p")
        long_column = f"search_threshold_{tag}_long"
        short_column = f"search_threshold_{tag}_short"
        frame[long_column] = (
            long_base
            & frame["osc"].gt(threshold)
            & frame["osc"].gt(frame["osc"].shift(1))
        )
        frame[short_column] = (
            short_base
            & frame["osc"].lt(-threshold)
            & frame["osc"].lt(frame["osc"].shift(1))
        )
        spec = Variant(f"threshold_{tag}", long_column, short_column)
        block_rows = []
        for period in DEVELOPMENT_BLOCKS:
            trades, marked = simulate_period(frame, spec, period, risk_percent=1.0)
            metric = summarize(
                trades,
                marked,
                variant=spec.name,
                period=period.name,
                risk_percent=1.0,
            )
            block_rows.append(metric)
            rows.append({"threshold": threshold, **metric})
        weighted = sum(r["trades"] * r["project_net_bp_per_trade"] for r in block_rows) / sum(
            r["trades"] for r in block_rows
        )
        for row in rows[-len(DEVELOPMENT_BLOCKS) :]:
            row["min_block_net_bp"] = min(r["project_net_bp_per_trade"] for r in block_rows)
            row["weighted_net_bp"] = weighted
            row["selected"] = threshold == 0.1
    return pd.DataFrame(rows)


def slope_search(frame: pd.DataFrame, lags: Iterable[int]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    fast = frame["fast_ma"]
    slow = frame["slow_ma"]
    cross_up = (fast > slow) & (fast.shift(1) <= slow.shift(1))
    cross_down = (fast < slow) & (fast.shift(1) >= slow.shift(1))
    for lag in lags:
        slope = frame["ema200"] / frame["ema200"].shift(lag) - 1.0
        long_column = f"search_slope_{lag}_long"
        short_column = f"search_slope_{lag}_short"
        frame[long_column] = (
            frame["osc_percentile_safe"]
            & cross_up
            & (frame["close"] > slow)
            & (frame["close"] > frame["regime_ma"])
            & slope.gt(0.0)
            & frame["osc"].gt(0.2)
            & frame["osc"].gt(frame["osc"].shift(1))
        )
        frame[short_column] = (
            frame["osc_percentile_safe"]
            & cross_down
            & (frame["close"] < slow)
            & (frame["close"] < frame["regime_ma"])
            & slope.lt(0.0)
            & frame["osc"].lt(-0.2)
            & frame["osc"].lt(frame["osc"].shift(1))
        )
        spec = Variant(f"slope_{lag}", long_column, short_column)
        block_rows = []
        for period in DEVELOPMENT_BLOCKS:
            trades, marked = simulate_period(frame, spec, period, risk_percent=1.0)
            metric = summarize(
                trades,
                marked,
                variant=spec.name,
                period=period.name,
                risk_percent=1.0,
            )
            block_rows.append(metric)
            rows.append({"slope_lag": lag, **metric})
        for row in rows[-len(DEVELOPMENT_BLOCKS) :]:
            row["min_block_net_bp"] = min(r["project_net_bp_per_trade"] for r in block_rows)
            row["weighted_net_bp"] = sum(
                r["trades"] * r["project_net_bp_per_trade"] for r in block_rows
            ) / sum(r["trades"] for r in block_rows)
            row["development_selected"] = lag == 72
            row["final_status"] = "failed_single_final_evaluation" if lag == 72 else "not_final_evaluated"
    return pd.DataFrame(rows)


def trailing_search(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    configurations: list[tuple[float | None, float | None]] = [(None, None)]
    configurations.extend(
        (trigger, distance)
        for trigger in (2.5, 4.0, 6.0, 8.0, 10.0)
        for distance in (1.0, 2.0, 3.0, 4.0, 5.0)
        if distance < trigger
    )
    for trigger, distance in configurations:
        enabled = trigger is not None
        params = SignalParameters(osc_threshold=0.1)
        if enabled:
            params = replace(
                params,
                trailing_trigger_percent=float(trigger),
                trailing_distance_percent=float(distance),
            )
        name = "trailing_off" if not enabled else f"trailing_{trigger:g}_{distance:g}"
        spec = Variant(
            name,
            "v9_long",
            "v9_short",
            trailing=enabled,
            params=params,
        )
        block_rows = []
        for period in DEVELOPMENT_BLOCKS:
            trades, marked = simulate_period(frame, spec, period, risk_percent=1.0)
            metric = summarize(
                trades,
                marked,
                variant=name,
                period=period.name,
                risk_percent=1.0,
            )
            block_rows.append(metric)
            rows.append(
                {
                    "trailing_enabled": enabled,
                    "trigger_percent": trigger,
                    "distance_percent": distance,
                    **metric,
                }
            )
        for row in rows[-len(DEVELOPMENT_BLOCKS) :]:
            row["min_block_net_bp"] = min(r["project_net_bp_per_trade"] for r in block_rows)
            row["weighted_net_bp"] = sum(
                r["trades"] * r["project_net_bp_per_trade"] for r in block_rows
            ) / sum(r["trades"] for r in block_rows)
            row["selected"] = not enabled
    return pd.DataFrame(rows)


def feature_filter_search(frame: pd.DataFrame) -> pd.DataFrame:
    """One-feature, natural-threshold screens on 2023/2024 only.

    The screen is deliberately rules-only: it does not fit LR/LightGBM and
    never reads the final-preholdout period to choose a filter.  Directional
    conditions use the same long/short semantics as the project's L2 feature
    alignment; non-directional conditions are identical for both sides.
    """

    both_true = pd.Series(True, index=frame.index)
    filters: dict[str, tuple[pd.Series, pd.Series]] = {
        "none": (both_true, both_true),
        "ema200_price_side": (frame["close"] > frame["ema200"], frame["close"] < frame["ema200"]),
        "ema55_price_side": (frame["close"] > frame["ema55"], frame["close"] < frame["ema55"]),
        "order_ge3": (frame["order_score"] >= 3, frame["down_order_score"] >= 3),
        "order_eq4": (frame["order_score"] == 4, frame["down_order_score"] == 4),
        "ret4_aligned": (frame["ret_4"] > 0, frame["ret_4"] < 0),
        "ret12_aligned": (frame["ret_12"] > 0, frame["ret_12"] < 0),
        "ret24_aligned": (frame["ret_24"] > 0, frame["ret_24"] < 0),
        "ret48_aligned": (frame["ret_48"] > 0, frame["ret_48"] < 0),
        "cluster_breakout": (frame["ext_up"] > 0, frame["ext_down"] > 0),
        "volume_ratio_ge1": (frame["volume_ratio"] >= 1, frame["volume_ratio"] >= 1),
        "vol_ratio_mean8_ge1": (frame["vol_ratio_mean8"] >= 1, frame["vol_ratio_mean8"] >= 1),
        "atr_ratio96_ge1": (frame["atr_pct_ratio96"] >= 1, frame["atr_pct_ratio96"] >= 1),
        "spread_expanding8": (frame["spread_chg8"] > 0, frame["spread_chg8"] > 0),
        "spread_converging8": (frame["spread_chg8"] < 0, frame["spread_chg8"] < 0),
        "strict_dense": (frame["ma_spread_pct"] <= 0.0028, frame["ma_spread_pct"] <= 0.0028),
        "not_strict_dense": (frame["ma_spread_pct"] > 0.0028, frame["ma_spread_pct"] > 0.0028),
        "pre_range48_le0032": (frame["pre_range48"] <= 0.032, frame["pre_range48"] <= 0.032),
    }
    rows: list[dict[str, Any]] = []
    for name, (long_gate, short_gate) in filters.items():
        long_column = f"feature_search_{name}_long"
        short_column = f"feature_search_{name}_short"
        frame[long_column] = frame["v9_long"] & long_gate.fillna(False)
        frame[short_column] = frame["v9_short"] & short_gate.fillna(False)
        spec = Variant(f"feature_{name}", long_column, short_column)
        block_rows = []
        for period in DEVELOPMENT_BLOCKS:
            trades, marked = simulate_period(frame, spec, period, risk_percent=1.0)
            metric = summarize(
                trades,
                marked,
                variant=spec.name,
                period=period.name,
                risk_percent=1.0,
            )
            block_rows.append(metric)
            rows.append({"feature_filter": name, **metric})
        weighted = sum(r["trades"] * r["project_net_bp_per_trade"] for r in block_rows) / sum(
            r["trades"] for r in block_rows
        )
        for row in rows[-len(DEVELOPMENT_BLOCKS) :]:
            row["min_block_net_bp"] = min(r["project_net_bp_per_trade"] for r in block_rows)
            row["weighted_net_bp"] = weighted
            row["development_selected"] = name == "vol_ratio_mean8_ge1"
            row["selection_note"] = (
                "selected only as a post-V9 forward hypothesis; final-preholdout was already consumed"
                if name == "vol_ratio_mean8_ge1"
                else "development diagnostic only"
            )
    return pd.DataFrame(rows)


def timeframe_rescale_ablation(raw: pd.DataFrame) -> pd.DataFrame:
    """Compare 15m bar-count windows with a 10m wall-clock-rescaled bundle.

    This is one conceptual variable: whether an original ten-minute window is
    translated to the nearest equal wall-clock duration on 15m bars.  ATR14,
    the 4x/3% stop and all cost/BE rules stay frozen.
    """

    params = SignalParameters(
        fast_len=7,
        slow_len=40,
        regime_len=67,
        atr_len=14,
        atr_mult=4.0,
        max_sl_percent=3.0,
        osc_basis_len=27,
        osc_percentile_len=133,
        osc_percentile=99.0,
        osc_change_lag=7,
        osc_hma_len=7,
        osc_threshold=0.1,
    )
    frame = add_pine_indicators(raw, params)
    project = add_features(add_project_indicators(raw))
    for column in project.columns:
        if column not in frame.columns:
            frame[column] = project[column].to_numpy()
    source = (frame["high"] + frame["low"]) / 2.0
    difference = source - source.rolling(27, min_periods=27).mean()
    percentile = difference.rolling(133, min_periods=133).quantile(0.99, interpolation="linear")
    safe = percentile.gt(0.0)
    fast = frame["fast_ma"]
    slow = frame["slow_ma"]
    frame["rescaled_long"] = (
        safe
        & (fast > slow)
        & (fast.shift(1) <= slow.shift(1))
        & (frame["close"] > slow)
        & (frame["close"] > frame["regime_ma"])
        & frame["slow_slope_12"].gt(0.0)
        & frame["osc"].gt(0.1)
        & frame["osc"].gt(frame["osc"].shift(1))
    )
    frame["rescaled_short"] = (
        safe
        & (fast < slow)
        & (fast.shift(1) >= slow.shift(1))
        & (frame["close"] < slow)
        & (frame["close"] < frame["regime_ma"])
        & frame["slow_slope_12"].lt(0.0)
        & frame["osc"].lt(-0.1)
        & frame["osc"].lt(frame["osc"].shift(1))
    )
    frame["rescaled_score"] = frame["osc"].abs().fillna(0.0)
    spec = Variant(
        "wallclock_rescaled_from_10m",
        "rescaled_long",
        "rescaled_short",
        score_column="rescaled_score",
        params=params,
    )
    rows = []
    for period in DEVELOPMENT_BLOCKS:
        trades, marked = simulate_period(frame, spec, period, risk_percent=1.0)
        rows.append(
            {
                "window_contract": "10m_wallclock_rescaled_to_15m",
                **summarize(
                    trades,
                    marked,
                    variant=spec.name,
                    period=period.name,
                    risk_percent=1.0,
                ),
            }
        )
    return pd.DataFrame(rows)


def cost_sensitivity(trades: pd.DataFrame) -> pd.DataFrame:
    rows = []
    final_name = "final_preholdout_2025_202602"
    for variant in ("v9_locked", "v10_volume_hypothesis"):
        selected = trades.loc[(trades["variant"] == variant) & (trades["split"] == final_name)]
        gross = float(selected["gross_return"].mean())
        for round_trip_cost in (0.0, 0.001, 0.002, 0.003, 0.004, 0.005, 0.006):
            rows.append(
                {
                    "variant": variant,
                    "round_trip_cost": round_trip_cost,
                    "round_trip_cost_bp": round_trip_cost * 10_000.0,
                    "gross_bp_per_trade": gross * 10_000.0,
                    "net_bp_per_trade": (gross - round_trip_cost) * 10_000.0,
                    "official_cost_row": round_trip_cost == 0.002,
                }
            )
    return pd.DataFrame(rows)


def make_charts(
    summaries: pd.DataFrame,
    equities: pd.DataFrame,
    trades: pd.DataFrame,
    thresholds: pd.DataFrame,
) -> None:
    CHARTS.mkdir(parents=True, exist_ok=True)
    final_name = "final_preholdout_2025_202602"
    subset = equities.loc[
        (equities["split"] == final_name)
        & equities["variant"].isin(
            ["v8_eth_baseline", "v9_locked", "v10_volume_hypothesis"]
        )
    ].copy()
    fig, ax = plt.subplots(figsize=(10, 5))
    for variant, group in subset.groupby("variant"):
        clean = group.sort_values("open_time").drop_duplicates("open_time", keep="last")
        ax.plot(clean["open_time"], clean["normalized_equity"], label=variant, linewidth=1.3)
    ax.set_title("ETH 15m final-preholdout marked equity (1% risk)")
    ax.set_ylabel("Normalized equity")
    ax.legend()
    ax.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(CHARTS / "final_equity_v8_vs_v9.png", dpi=160)
    plt.close(fig)

    selected = trades.loc[
        (trades["variant"] == "v9_locked") & (trades["split"] == final_name)
    ].copy()
    selected["month"] = (
        pd.to_datetime(selected["entry_time"], utc=True).dt.tz_localize(None).dt.to_period("M").astype(str)
    )
    monthly = selected.groupby("month")["project_net_return"].mean().mul(10_000.0)
    fig, ax = plt.subplots(figsize=(10, 4.5))
    monthly.plot.bar(ax=ax, color=["#6B8E23" if value > 0 else "#C2417A" for value in monthly])
    ax.axhline(0.0, color="#334155", linewidth=0.8)
    ax.set_title("V9 final-preholdout monthly unit net expectancy")
    ax.set_ylabel("bp / trade after 20bp cost")
    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout()
    fig.savefig(CHARTS / "v9_monthly_net_bp.png", dpi=160)
    plt.close(fig)

    collapsed = thresholds.groupby("threshold", as_index=False).first()
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(collapsed["threshold"], collapsed["weighted_net_bp"], marker="o", label="weighted")
    ax.plot(collapsed["threshold"], collapsed["min_block_net_bp"], marker="s", label="worst half-year")
    ax.axvline(0.1, color="#6B8E23", linestyle="--", label="locked 0.1")
    ax.axhline(0.0, color="#334155", linewidth=0.8)
    ax.set_title("Oscillator threshold robustness (2023–2024 only)")
    ax.set_xlabel("Absolute threshold")
    ax.set_ylabel("Net bp / trade")
    ax.legend()
    ax.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(CHARTS / "oscillator_threshold_robustness.png", dpi=160)
    plt.close(fig)


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def run(config_path: Path = CONFIG_PATH, output_dir: Path = RESULTS) -> dict[str, Any]:
    global RESULTS, CHARTS
    RESULTS = output_dir
    CHARTS = RESULTS / "charts"
    RESULTS.mkdir(parents=True, exist_ok=True)
    config = load_config(config_path)
    raw, quality = load_research_frame(config)
    frame = build_feature_frame(raw)
    featured = add_features(add_project_indicators(raw))

    summary_rows: list[dict[str, Any]] = []
    all_trades: list[pd.DataFrame] = []
    all_equities: list[pd.DataFrame] = []
    matrix_rows: list[dict[str, Any]] = []
    specs = variants()
    for spec in specs:
        for period in SPLITS:
            if period.name.startswith("final_") and not spec.final_evaluated:
                continue
            trades, marked = simulate_period(frame, spec, period, risk_percent=1.0)
            summary_rows.append(
                summarize(
                    trades,
                    marked,
                    variant=spec.name,
                    period=period.name,
                    risk_percent=1.0,
                )
            )
            if not trades.empty:
                all_trades.append(trades)
            if not marked.empty:
                all_equities.append(marked)
        for period in DEVELOPMENT_BLOCKS:
            trades, marked = simulate_period(frame, spec, period, risk_percent=1.0)
            matrix_rows.append(
                {
                    "selection_status": spec.selection_status,
                    **summarize(
                        trades,
                        marked,
                        variant=spec.name,
                        period=period.name,
                        risk_percent=1.0,
                    ),
                }
            )

    summaries = pd.DataFrame(summary_rows)
    trades = pd.concat(all_trades, ignore_index=True)
    equities = pd.concat(all_equities, ignore_index=True)
    experiment_matrix = pd.DataFrame(matrix_rows)

    risk_rows: list[dict[str, Any]] = []
    locked = next(spec for spec in specs if spec.name == "v9_locked")
    for risk in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
        for period in SPLITS:
            risk_trades, risk_equity = simulate_period(frame, locked, period, risk_percent=risk)
            risk_rows.append(
                summarize(
                    risk_trades,
                    risk_equity,
                    variant="v9_locked",
                    period=period.name,
                    risk_percent=risk,
                )
            )
    risk_grid = pd.DataFrame(risk_rows)

    threshold_results = threshold_search(
        frame, config["selection_log"]["oscillator_thresholds_searched"]
    )
    slope_results = slope_search(frame, config["selection_log"]["slope_lags_searched"])
    trailing_results = trailing_search(frame)
    feature_filter_results = feature_filter_search(frame)
    timeframe_results = timeframe_rescale_ablation(raw)
    cost_results = cost_sensitivity(trades)

    final_period = next(period for period in SPLITS if period.name.startswith("final_"))
    final_trades = trades.loc[
        (trades["variant"] == "v9_locked") & (trades["split"] == final_period.name)
    ].copy()
    controls = build_matched_controls(frame, final_trades, final_period)
    pairs = pair_controls(final_trades, controls)
    signflip = block_signflip(pairs)
    excess_ci = week_bootstrap_ci(pairs, "excess_return")
    absolute_for_ci = pairs.rename(columns={"project_net_return": "absolute_return"})
    absolute_ci = week_bootstrap_ci(absolute_for_ci, "absolute_return", seed=20260823)
    ranking = permutation_test(
        final_trades["score"].to_numpy(dtype=float),
        final_trades["project_net_return"].to_numpy(dtype=float),
        n_permutations=N_RESAMPLES,
        seed=20260824,
    )

    v10_trades = trades.loc[
        (trades["variant"] == "v10_volume_hypothesis")
        & (trades["split"] == final_period.name)
    ].copy()
    v10_controls = build_matched_controls(
        frame,
        v10_trades,
        final_period,
        seed="pine-eth15m-v10-volume-controls-v1",
    )
    v10_pairs = pair_controls(v10_trades, v10_controls)
    v10_signflip = block_signflip(v10_pairs, seed=20260825)
    v10_excess_ci = week_bootstrap_ci(v10_pairs, "excess_return", seed=20260826)
    v10_absolute_ci = week_bootstrap_ci(
        v10_pairs.rename(columns={"project_net_return": "absolute_return"}),
        "absolute_return",
        seed=20260827,
    )

    l2_rows = attach_l2_features(featured, final_trades)
    feature_contract = {
        "pine_features": [
            "hl2",
            "sma_hl2_10",
            "sma_hl2_60",
            "ema_close_100",
            "pine_atr14",
            "atr_percent",
            "hl2_minus_sma40",
            "rolling_200_p99_difference",
            "ratio_change_10",
            "hma10_oscillator",
            "ema200_slope_12",
            "HK hour/day filters",
        ],
        "project_l2_features": FEATURE_COLUMNS,
        "feature_rows": int(len(l2_rows)),
        "training_eligible": False,
        "existing_frozen_model_scored": False,
    }
    statistics = {
        "matched_control": {
            "candidate_trades": int(len(final_trades)),
            "paired_trades": int(len(pairs)),
            "control_rows": int(len(controls)),
            "minimum_controls_per_pair": int(pairs["control_count"].min()) if len(pairs) else 0,
            "mean_candidate_net_bp": float(pairs["project_net_return"].mean() * 10_000.0),
            "mean_control_net_bp": float(pairs["control_mean_project_net"].mean() * 10_000.0),
            "mean_excess_bp": float(pairs["excess_return"].mean() * 10_000.0),
            "duplicate_control_starts": int(controls["control_signal_i"].duplicated().sum()) if len(controls) else 0,
            "controls_outside_split": int((controls["control_exit_i"] > np.flatnonzero(_period_mask(frame, final_period))[-1]).sum()) if len(controls) else 0,
        },
        "week_block_signflip": signflip,
        "week_bootstrap_excess": excess_ci,
        "week_bootstrap_absolute": absolute_ci,
        "oscillator_ranking_permutation": {
            "auc_net_positive": auc_from_scores(
                final_trades["score"].to_numpy(dtype=float),
                final_trades["project_net_return"].gt(0.0).to_numpy(dtype=bool),
            ),
            "top_decile_net_bp": ranking.statistic * 10_000.0,
            "p_value": ranking.p_value,
            "null_mean_bp": ranking.null_mean * 10_000.0,
            "null_std_bp": ranking.null_std * 10_000.0,
            "n_samples": ranking.n_samples,
            "n_permutations": ranking.n_permutations,
        },
        "profit_concentration": concentration_diagnostics(final_trades),
        "v10_post_selection_hypothesis": {
            "matched_control": {
                "candidate_trades": int(len(v10_trades)),
                "paired_trades": int(len(v10_pairs)),
                "control_rows": int(len(v10_controls)),
                "mean_candidate_net_bp": float(v10_pairs["project_net_return"].mean() * 10_000.0),
                "mean_control_net_bp": float(v10_pairs["control_mean_project_net"].mean() * 10_000.0),
                "mean_excess_bp": float(v10_pairs["excess_return"].mean() * 10_000.0),
            },
            "week_block_signflip": v10_signflip,
            "week_bootstrap_excess": v10_excess_ci,
            "week_bootstrap_absolute": v10_absolute_ci,
            "profit_concentration": concentration_diagnostics(v10_trades),
            "status": "post-final-selection forward hypothesis; not an independent OOS result",
        },
    }

    selected_summary = summaries.loc[
        (summaries["variant"] == "v9_locked") & (summaries["period"] == final_period.name)
    ].iloc[0].to_dict()
    baseline_summary = summaries.loc[
        (summaries["variant"] == "v8_eth_baseline") & (summaries["period"] == final_period.name)
    ].iloc[0].to_dict()
    v10_summary = summaries.loc[
        (summaries["variant"] == "v10_volume_hypothesis")
        & (summaries["period"] == final_period.name)
    ].iloc[0].to_dict()
    summary = {
        "experiment_id": config["experiment_id"],
        "generated_from_commit": current_commit(),
        "holdout_consumed": False,
        "tradingview_parity_passed": False,
        "research_data": quality,
        "baseline_final_preholdout": baseline_summary,
        "v9_final_preholdout": selected_summary,
        "v10_post_selection_final_preholdout": v10_summary,
        "statistics": statistics,
        "honest_verdict": (
            "V9 improves the consumed final-preholdout unit net expectancy, but absolute week-bootstrap "
            "uncertainty, matched-control significance, monthly concentration, TradingView parity and fresh "
            "forward evidence determine whether it is more than a research candidate."
        ),
    }

    summaries.to_csv(RESULTS / "split_summary.csv", index=False)
    experiment_matrix.to_csv(RESULTS / "experiment_matrix.csv", index=False)
    trades.to_csv(RESULTS / "trades.csv", index=False)
    equities.to_csv(RESULTS / "bar_equity.csv", index=False)
    risk_grid.to_csv(RESULTS / "risk_grid.csv", index=False)
    threshold_results.to_csv(RESULTS / "threshold_search.csv", index=False)
    slope_results.to_csv(RESULTS / "slope_search.csv", index=False)
    trailing_results.to_csv(RESULTS / "trailing_search.csv", index=False)
    feature_filter_results.to_csv(RESULTS / "feature_filter_search.csv", index=False)
    timeframe_results.to_csv(RESULTS / "timeframe_rescale_ablation.csv", index=False)
    cost_results.to_csv(RESULTS / "cost_sensitivity.csv", index=False)
    controls.to_csv(RESULTS / "matched_controls.csv", index=False)
    pairs.to_csv(RESULTS / "matched_pairs.csv", index=False)
    v10_controls.to_csv(RESULTS / "v10_matched_controls.csv", index=False)
    v10_pairs.to_csv(RESULTS / "v10_matched_pairs.csv", index=False)
    l2_rows.to_csv(RESULTS / "pine_l2_feature_rows.csv", index=False)
    write_json(RESULTS / "data_quality.json", quality)
    write_json(RESULTS / "feature_contract.json", feature_contract)
    write_json(RESULTS / "statistical_tests.json", statistics)
    write_json(RESULTS / "summary.json", summary)
    make_charts(summaries, equities, trades, threshold_results)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--output", type=Path, default=RESULTS)
    args = parser.parse_args()
    summary = run(args.config, args.output)
    selected = summary["v9_final_preholdout"]
    print(
        json.dumps(
            {
                "experiment_id": summary["experiment_id"],
                "trades": selected["trades"],
                "net_bp_per_trade": selected["project_net_bp_per_trade"],
                "return_percent": selected["return_percent"],
                "max_drawdown_15m_percent": selected["max_drawdown_15m_percent"],
                "holdout_consumed": summary["holdout_consumed"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
