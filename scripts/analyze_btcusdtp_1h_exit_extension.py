#!/usr/bin/env python3
"""Replay predeclared exit-only arms on the frozen BTCUSDT.P 1h signals.

Signal identity, entry, and original stop come from ``trade_ledger.csv``.  The
only future columns read are the next 12 confirmed 1h OHLC bars, used to resolve
fixed 3R/4R/5R/6R exits and two predeclared 50% scale-out arms.  Matched-control
entries reuse ``control_signal_i``, direction, and copied ATR stop distance from
``matched_controls.csv``.  No feature, signal, threshold, or model is fitted;
this is configuration-specific holdout use 2 and remains post-hoc exploratory.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from scripts.backtest_two_key_candle_pine_v8_btc_1h import (
    _holm_adjust,
    bootstrap_mean_ci,
    load_config,
    load_hourly_source,
    signflip_p,
    write_json,
)
from scripts.research_two_key_candle_ma_retest_1h import add_features, sha256_file

PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = (
    PROJECT
    / "experiments/active/exp-btcusdtp-1h-pine-v8-sixmonth-backtest-20260904-v1"
)
RESULTS = EXPERIMENT / "results"
PROTOCOL = EXPERIMENT / "protocol_amendment_02_exit_extension.json"
SOURCE = RESULTS / "source/okx_BTC_USDT_SWAP_1H.csv.gz"
EVENTS = RESULTS / "trade_ledger.csv"
CONTROLS = RESULTS / "matched_controls.csv"
SUMMARY_OUT = RESULTS / "exit_target_summary.csv"
TRADES_OUT = RESULTS / "exit_target_trade_ledger.csv"
COMPARISONS_OUT = RESULTS / "exit_target_comparisons.csv"
PERIODS_OUT = RESULTS / "exit_target_periods.csv"
CONTINUATION_OUT = RESULTS / "exit_target_continuation.csv"
JSON_OUT = RESULTS / "exit_target_diagnostics.json"
CHART_OUT = RESULTS / "exit_target_diagnostics.png"

TEAL = "#17A297"
TEAL_LIGHT = "#BFE7E2"
ORANGE = "#F59E0B"
ORANGE_LIGHT = "#FBD7A0"
INK = "#26323A"
MUTED = "#7A858D"
GRID = "#D9DEE1"

ARM_LABELS = {
    "fixed_3R": "Fixed 3R",
    "fixed_4R": "Fixed 4R",
    "fixed_5R": "Fixed 5R",
    "fixed_6R": "Fixed 6R",
    "split_3R_6R": "50% 3R + 50% 6R",
    "split_3R_runner": "50% 3R + runner",
}
ARM_ORDER = list(ARM_LABELS)
FIXED_TARGETS = {"fixed_3R": 3.0, "fixed_4R": 4.0, "fixed_5R": 5.0, "fixed_6R": 6.0}


@dataclass(frozen=True)
class ExitResult:
    outcome: str
    exit_i: int
    hold_bars: int
    exit_price: float
    gross_return: float
    net_return: float
    return_r: float
    net_return_r: float
    mfe_r: float
    mae_r: float
    scaled_at_3r: bool


def _verify_inputs(protocol: dict[str, Any]) -> None:
    """Fail closed if any source declared before the replay has drifted."""

    if protocol.get("status") != "frozen_before_exit-arm_replay":
        raise RuntimeError("exit-extension protocol is not frozen")
    if int(protocol["owner_authorization"]["configuration_holdout_use"]) != 2:
        raise RuntimeError("exit-extension protocol must be holdout use 2")
    declared = protocol["fixed_inputs"]
    checks = [
        (SOURCE, declared["source_sha256"]),
        (EVENTS, declared["signal_ledger_sha256"]),
        (CONTROLS, declared["matched_controls_sha256"]),
    ]
    for path, expected in checks:
        actual = sha256_file(path)
        if actual != expected:
            raise RuntimeError(f"input drift for {path}: expected {expected}, got {actual}")


def _excursions(
    *, high: float, low: float, entry: float, risk: float, direction: int
) -> tuple[float, float]:
    favourable = high - entry if direction > 0 else entry - low
    adverse = entry - low if direction > 0 else high - entry
    return favourable / risk, adverse / risk


def resolve_fixed(
    frame: pd.DataFrame,
    *,
    entry_i: int,
    direction: int,
    entry_price: float,
    risk_price: float,
    target_r: float,
    horizon: int,
    cost: float,
) -> ExitResult:
    """Resolve one fixed target with the frozen conservative stop-first rule."""

    stop = entry_price - direction * risk_price
    target = entry_price + direction * risk_price * target_r
    if entry_i + horizon > len(frame):
        raise RuntimeError("fixed-target path is incomplete")
    mfe = 0.0
    mae = 0.0
    for local, i in enumerate(range(entry_i, entry_i + horizon), start=1):
        high = float(frame.loc[i, "high"])
        low = float(frame.loc[i, "low"])
        fav_r, adv_r = _excursions(
            high=high,
            low=low,
            entry=entry_price,
            risk=risk_price,
            direction=direction,
        )
        mfe = max(mfe, fav_r)
        mae = max(mae, adv_r)
        hit_stop = low <= stop if direction > 0 else high >= stop
        hit_target = high >= target if direction > 0 else low <= target
        if hit_stop:
            exit_price = stop
            outcome = "stop_collision" if hit_target else "stop"
            break
        if hit_target:
            exit_price = target
            outcome = "target"
            break
    else:
        i = entry_i + horizon - 1
        local = horizon
        exit_price = float(frame.loc[i, "close"])
        outcome = "timeout"
    return_r = direction * (exit_price - entry_price) / risk_price
    gross = return_r * risk_price / entry_price
    net = gross - cost
    return ExitResult(
        outcome=outcome,
        exit_i=int(i),
        hold_bars=int(local),
        exit_price=float(exit_price),
        gross_return=float(gross),
        net_return=float(net),
        return_r=float(return_r),
        net_return_r=float(net / (risk_price / entry_price)),
        mfe_r=float(mfe),
        mae_r=float(mae),
        scaled_at_3r=False,
    )


def resolve_split(
    frame: pd.DataFrame,
    *,
    entry_i: int,
    direction: int,
    entry_price: float,
    risk_price: float,
    second_target_r: float | None,
    horizon: int,
    cost: float,
) -> ExitResult:
    """Resolve a 50% 3R scale-out with the original stop on the remainder."""

    stop = entry_price - direction * risk_price
    first_target = entry_price + direction * risk_price * 3.0
    second_target = (
        entry_price + direction * risk_price * second_target_r
        if second_target_r is not None
        else None
    )
    if entry_i + horizon > len(frame):
        raise RuntimeError("split-target path is incomplete")
    scaled = False
    mfe = 0.0
    mae = 0.0
    for local, i in enumerate(range(entry_i, entry_i + horizon), start=1):
        high = float(frame.loc[i, "high"])
        low = float(frame.loc[i, "low"])
        fav_r, adv_r = _excursions(
            high=high,
            low=low,
            entry=entry_price,
            risk=risk_price,
            direction=direction,
        )
        mfe = max(mfe, fav_r)
        mae = max(mae, adv_r)
        hit_stop = low <= stop if direction > 0 else high >= stop
        hit_first = high >= first_target if direction > 0 else low <= first_target
        hit_second = (
            (high >= second_target if direction > 0 else low <= second_target)
            if second_target is not None
            else False
        )
        if not scaled:
            if hit_stop:
                return_r = -1.0
                exit_price = stop
                outcome = "stop_before_scale_collision" if hit_first else "stop_before_scale"
                break
            if hit_first:
                scaled = True
                if hit_second:
                    return_r = 0.5 * 3.0 + 0.5 * float(second_target_r)
                    exit_price = float(second_target)
                    outcome = "full_target"
                    break
        else:
            if hit_stop:
                return_r = 0.5 * 3.0 + 0.5 * -1.0
                exit_price = stop
                outcome = "scale_then_stop_collision" if hit_second else "scale_then_stop"
                break
            if hit_second:
                return_r = 0.5 * 3.0 + 0.5 * float(second_target_r)
                exit_price = float(second_target)
                outcome = "full_target"
                break
    else:
        i = entry_i + horizon - 1
        local = horizon
        close = float(frame.loc[i, "close"])
        close_r = direction * (close - entry_price) / risk_price
        if scaled:
            return_r = 0.5 * 3.0 + 0.5 * close_r
            outcome = "scale_then_timeout"
        else:
            return_r = close_r
            outcome = "timeout_before_scale"
        exit_price = close
    gross = float(return_r) * risk_price / entry_price
    net = gross - cost
    return ExitResult(
        outcome=outcome,
        exit_i=int(i),
        hold_bars=int(local),
        exit_price=float(exit_price),
        gross_return=float(gross),
        net_return=float(net),
        return_r=float(return_r),
        net_return_r=float(net / (risk_price / entry_price)),
        mfe_r=float(mfe),
        mae_r=float(mae),
        scaled_at_3r=bool(scaled),
    )


def _result_row(
    *,
    subject_type: str,
    event_id: str,
    control_rank: int | None,
    arm: str,
    entry_time: pd.Timestamp,
    entry_i: int,
    direction: int,
    entry_price: float,
    risk_price: float,
    result: ExitResult,
) -> dict[str, Any]:
    final_family = (
        "target"
        if result.outcome in {"target", "full_target"}
        else "stop"
        if "stop" in result.outcome
        else "timeout"
    )
    return {
        "subject_type": subject_type,
        "candidate_event_id": event_id,
        "control_rank": control_rank,
        "arm": arm,
        "arm_label": ARM_LABELS[arm],
        "entry_time": entry_time,
        "entry_i": entry_i,
        "direction": direction,
        "entry_price": entry_price,
        "risk_price": risk_price,
        "risk_pct": risk_price / entry_price,
        "outcome": result.outcome,
        "exit_family": final_family,
        "exit_i": result.exit_i,
        "hold_bars": result.hold_bars,
        "exit_price": result.exit_price,
        "gross_return": result.gross_return,
        "net_return": result.net_return,
        "return_r": result.return_r,
        "net_return_r": result.net_return_r,
        "mfe_r": result.mfe_r,
        "mae_r": result.mae_r,
        "scaled_at_3r": result.scaled_at_3r,
        "net_profitable": result.net_return > 0.0,
    }


def _simulate_arms(
    frame: pd.DataFrame,
    subjects: pd.DataFrame,
    *,
    subject_type: str,
    horizon: int,
    cost: float,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for subject in subjects.to_dict("records"):
        for arm in ARM_ORDER:
            common = {
                "frame": frame,
                "entry_i": int(subject["entry_i"]),
                "direction": int(subject["direction"]),
                "entry_price": float(subject["entry_price"]),
                "risk_price": float(subject["risk_price"]),
                "horizon": horizon,
                "cost": cost,
            }
            if arm in FIXED_TARGETS:
                result = resolve_fixed(**common, target_r=FIXED_TARGETS[arm])
            elif arm == "split_3R_6R":
                result = resolve_split(**common, second_target_r=6.0)
            else:
                result = resolve_split(**common, second_target_r=None)
            rows.append(
                _result_row(
                    subject_type=subject_type,
                    event_id=str(subject["candidate_event_id"]),
                    control_rank=(
                        int(subject["control_rank"])
                        if pd.notna(subject.get("control_rank"))
                        else None
                    ),
                    arm=arm,
                    entry_time=pd.Timestamp(subject["entry_time"]),
                    entry_i=int(subject["entry_i"]),
                    direction=int(subject["direction"]),
                    entry_price=float(subject["entry_price"]),
                    risk_price=float(subject["risk_price"]),
                    result=result,
                )
            )
    return pd.DataFrame(rows)


def _subjects(
    events: pd.DataFrame, controls: pd.DataFrame, featured: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    candidate = events.rename(columns={"event_id": "candidate_event_id"})[
        ["candidate_event_id", "entry_time", "entry_i", "direction", "entry_price", "risk_price"]
    ].copy()
    candidate["control_rank"] = np.nan
    control_rows: list[dict[str, Any]] = []
    for row in controls.to_dict("records"):
        signal_i = int(row["control_signal_i"])
        entry_i = signal_i + 1
        entry_price = float(featured.loc[entry_i, "open"])
        risk_price = float(row["copied_stop_distance_atr"]) * float(
            featured.loc[signal_i, "atr"]
        )
        control_rows.append(
            {
                "candidate_event_id": row["candidate_event_id"],
                "control_rank": int(row["control_rank"]),
                "entry_time": featured.loc[entry_i, "open_time"],
                "entry_i": entry_i,
                "direction": int(row["direction"]),
                "entry_price": entry_price,
                "risk_price": risk_price,
            }
        )
    return candidate, pd.DataFrame(control_rows)


def _assert_baseline_parity(
    simulated: pd.DataFrame,
    source: pd.DataFrame,
    *,
    subject_type: str,
) -> None:
    baseline = simulated[simulated["arm"].eq("fixed_3R")].copy()
    if subject_type == "candidate":
        expected = source.rename(columns={"event_id": "candidate_event_id"}).copy()
        keys = ["candidate_event_id"]
    else:
        expected = source.copy()
        keys = ["candidate_event_id", "control_rank"]
    merged = baseline.merge(expected, on=keys, suffixes=("_new", "_old"), validate="one_to_one")
    if len(merged) != len(expected):
        raise RuntimeError(f"{subject_type} 3R parity row count mismatch")
    old_family = np.where(
        merged["outcome_old"].eq("tp"),
        "target",
        np.where(merged["outcome_old"].astype(str).str.startswith("sl"), "stop", "timeout"),
    )
    checks = {
        "exit_family": np.asarray(merged["exit_family"].eq(old_family)),
        "exit_i": np.asarray(merged["exit_i_new"].astype(int).eq(merged["exit_i_old"].astype(int))),
        "hold_bars": np.asarray(
            merged["hold_bars_new"].astype(int).eq(merged["hold_bars_old"].astype(int))
        ),
        "net_return": np.isclose(merged["net_return_new"], merged["net_return_old"], atol=1e-12),
    }
    failed = [name for name, values in checks.items() if not bool(np.all(values))]
    if failed:
        raise RuntimeError(f"{subject_type} fixed-3R parity failed: {failed}")


def _profit_factor(values: pd.Series) -> float:
    positive = float(values[values > 0.0].sum())
    negative = float(-values[values < 0.0].sum())
    return positive / negative if negative > 0.0 else float("inf")


def _compounded(values: pd.Series) -> float:
    return float(np.prod(1.0 + values.to_numpy(dtype=float)) - 1.0)


def _drawdown(factors: np.ndarray) -> float:
    equity = np.cumprod(factors)
    prior_peak = np.maximum.accumulate(np.r_[1.0, equity])[:-1]
    return float(np.min(equity / prior_peak - 1.0))


def _summaries(trades: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    candidates = trades[trades["subject_type"].eq("candidate")]
    controls = trades[trades["subject_type"].eq("control")]
    for arm in ARM_ORDER:
        group = candidates[candidates["arm"].eq(arm)].sort_values("entry_time")
        group_ex_sep = group[pd.to_datetime(group["entry_time"], utc=True).lt("2026-09-01T00:00:00Z")]
        control = controls[controls["arm"].eq(arm)]
        per_event_control = control.groupby("candidate_event_id")["net_return"].mean()
        matched = group[group["candidate_event_id"].isin(per_event_control.index)].copy()
        excess = matched["net_return"].to_numpy(dtype=float) - matched[
            "candidate_event_id"
        ].map(per_event_control).to_numpy(dtype=float)
        risk_factors = 1.0 + 0.01 * group["net_return_r"].to_numpy(dtype=float)
        risk_factors_ex_sep = 1.0 + 0.01 * group_ex_sep["net_return_r"].to_numpy(dtype=float)
        rows.append(
            {
                "arm": arm,
                "arm_label": ARM_LABELS[arm],
                "n": len(group),
                "target_count": int(group["exit_family"].eq("target").sum()),
                "stop_count": int(group["exit_family"].eq("stop").sum()),
                "timeout_count": int(group["exit_family"].eq("timeout").sum()),
                "scaled_count": int(group["scaled_at_3r"].sum()),
                "net_win_count": int(group["net_profitable"].sum()),
                "net_win_rate": float(group["net_profitable"].mean()),
                "mean_net_bp": float(group["net_return"].mean() * 10_000.0),
                "median_net_bp": float(group["net_return"].median() * 10_000.0),
                "profit_factor": _profit_factor(group["net_return"]),
                "equal_notional_compounded": _compounded(group["net_return"]),
                "equal_risk_1pct_compounded": float(np.prod(risk_factors) - 1.0),
                "equal_risk_1pct_max_drawdown": _drawdown(risk_factors),
                "excluding_partial_september_n": len(group_ex_sep),
                "excluding_partial_september_mean_net_bp": float(
                    group_ex_sep["net_return"].mean() * 10_000.0
                ),
                "excluding_partial_september_profit_factor": _profit_factor(
                    group_ex_sep["net_return"]
                ),
                "excluding_partial_september_equal_risk_1pct_compounded": float(
                    np.prod(risk_factors_ex_sep) - 1.0
                ),
                "excluding_partial_september_equal_risk_1pct_max_drawdown": _drawdown(
                    risk_factors_ex_sep
                ),
                "control_n": len(control),
                "control_mean_net_bp": float(control["net_return"].mean() * 10_000.0),
                "matched_candidate_n": len(matched),
                "candidate_minus_control_bp": float(excess.mean() * 10_000.0),
            }
        )
    return pd.DataFrame(rows)


def _comparisons(trades: pd.DataFrame, protocol: dict[str, Any]) -> pd.DataFrame:
    candidate = trades[trades["subject_type"].eq("candidate")]
    pivot = candidate.pivot(index="candidate_event_id", columns="arm", values="net_return")
    event_time = (
        candidate[candidate["arm"].eq("fixed_3R")]
        .set_index("candidate_event_id")["entry_time"]
        .map(lambda value: pd.Timestamp(value))
    )
    rows: list[dict[str, Any]] = []
    resamples = int(load_config()["evaluation"]["permutation_resamples"])
    bootstrap_resamples = int(load_config()["evaluation"]["bootstrap_resamples"])
    for index, arm in enumerate(ARM_ORDER[1:], start=1):
        delta = (pivot[arm] - pivot["fixed_3R"]).to_numpy(dtype=float)
        delta_series = pivot[arm] - pivot["fixed_3R"]
        pre = event_time.lt("2026-05-04T00:00:00Z")
        post_ex_sep = event_time.ge("2026-05-04T00:00:00Z") & event_time.lt(
            "2026-09-01T00:00:00Z"
        )
        partial_sep = event_time.ge("2026-09-01T00:00:00Z")
        ci_low, ci_high = bootstrap_mean_ci(
            delta,
            resamples=bootstrap_resamples,
            seed=2026090410 + index,
        )
        rows.append(
            {
                "arm": arm,
                "arm_label": ARM_LABELS[arm],
                "n": len(delta),
                "mean_delta_bp_vs_3r": float(delta.mean() * 10_000.0),
                "median_delta_bp_vs_3r": float(np.median(delta) * 10_000.0),
                "better_count": int(np.sum(delta > 1e-15)),
                "worse_count": int(np.sum(delta < -1e-15)),
                "equal_count": int(np.sum(np.abs(delta) <= 1e-15)),
                "signflip_p_one_sided": signflip_p(
                    delta,
                    resamples=resamples,
                    seed=2026090420 + index,
                ),
                "bootstrap_95_ci_low_bp": ci_low * 10_000.0,
                "bootstrap_95_ci_high_bp": ci_high * 10_000.0,
                "pre_boundary_mean_delta_bp": float(delta_series[pre].mean() * 10_000.0),
                "post_boundary_ex_sep_mean_delta_bp": float(
                    delta_series[post_ex_sep].mean() * 10_000.0
                ),
                "partial_september_mean_delta_bp": float(
                    delta_series[partial_sep].mean() * 10_000.0
                ),
                "delta_positive_both_nonpartial_periods": bool(
                    delta_series[pre].mean() > 0.0 and delta_series[post_ex_sep].mean() > 0.0
                ),
                "posthoc": True,
                "promotable": False,
                "holdout_use": int(
                    protocol["owner_authorization"]["configuration_holdout_use"]
                ),
            }
        )
    result = pd.DataFrame(rows)
    fixed = result["arm"].isin(["fixed_4R", "fixed_5R", "fixed_6R"])
    result["holm_p_fixed_targets"] = np.nan
    result.loc[fixed, "holm_p_fixed_targets"] = _holm_adjust(
        result.loc[fixed, "signflip_p_one_sided"].tolist()
    )
    return result


def _periods(trades: pd.DataFrame) -> pd.DataFrame:
    candidate = trades[trades["subject_type"].eq("candidate")].copy()
    candidate["entry_time"] = pd.to_datetime(candidate["entry_time"], utc=True)
    conditions = [
        candidate["entry_time"].lt("2026-05-04T00:00:00Z"),
        candidate["entry_time"].ge("2026-05-04T00:00:00Z")
        & candidate["entry_time"].lt("2026-09-01T00:00:00Z"),
        candidate["entry_time"].ge("2026-09-01T00:00:00Z"),
    ]
    candidate["period"] = np.select(
        conditions,
        ["pre_boundary", "post_boundary_ex_sep", "partial_september"],
        default="outside",
    )
    rows: list[dict[str, Any]] = []
    for (arm, period), group in candidate.groupby(["arm", "period"], sort=False):
        rows.append(
            {
                "arm": arm,
                "arm_label": ARM_LABELS[arm],
                "period": period,
                "n": len(group),
                "net_win_count": int(group["net_profitable"].sum()),
                "net_win_rate": float(group["net_profitable"].mean()),
                "mean_net_bp": float(group["net_return"].mean() * 10_000.0),
                "profit_factor": _profit_factor(group["net_return"]),
            }
        )
    return pd.DataFrame(rows)


def _continuation(trades: pd.DataFrame) -> pd.DataFrame:
    candidate = trades[trades["subject_type"].eq("candidate")].copy()
    baseline = candidate[
        candidate["arm"].eq("fixed_3R") & candidate["exit_family"].eq("target")
    ][["candidate_event_id", "entry_time", "direction", "hold_bars", "net_return"]].rename(
        columns={"hold_bars": "fixed_3r_hold_bars", "net_return": "fixed_3r_net_return"}
    )
    rows = baseline.copy()
    for arm in ["fixed_4R", "fixed_5R", "fixed_6R", "split_3R_6R", "split_3R_runner"]:
        part = candidate[candidate["arm"].eq(arm)][
            ["candidate_event_id", "outcome", "exit_family", "hold_bars", "net_return", "return_r"]
        ].rename(columns={column: f"{arm}_{column}" for column in ["outcome", "exit_family", "hold_bars", "net_return", "return_r"]})
        rows = rows.merge(part, on="candidate_event_id", validate="one_to_one")
    return rows.sort_values("entry_time").reset_index(drop=True)


def _plot(summary: pd.DataFrame, output: Path) -> None:
    """Render the predeclared arm comparison and fixed-target hit-rate tradeoff."""

    figure, axes = plt.subplots(1, 2, figsize=(15, 6.8), constrained_layout=True)
    figure.patch.set_facecolor("white")
    x = np.arange(len(summary))
    width = 0.36
    axes[0].bar(
        x - width / 2,
        summary["mean_net_bp"],
        width,
        color=TEAL,
        edgecolor=INK,
        linewidth=0.7,
        label="K1→K2 candidates",
    )
    axes[0].bar(
        x + width / 2,
        summary["control_mean_net_bp"],
        width,
        color=TEAL_LIGHT,
        edgecolor=INK,
        linewidth=0.7,
        hatch="//",
        label="Exact matched controls",
    )
    axes[0].axhline(0.0, color=INK, linewidth=0.9)
    axes[0].set_xticks(x, summary["arm_label"], rotation=25, ha="right")
    axes[0].set_ylabel("Mean net return (bp / trade)")
    axes[0].set_title("Post-cost return by predeclared exit arm", loc="left", weight="bold")
    axes[0].legend(frameon=False, loc="upper left")
    for offset, column in [(-width / 2, "mean_net_bp"), (width / 2, "control_mean_net_bp")]:
        for position, value in zip(x + offset, summary[column]):
            axes[0].text(
                position,
                value + (1.2 if value >= 0 else -1.2),
                f"{value:+.1f}",
                ha="center",
                va="bottom" if value >= 0 else "top",
                fontsize=8,
                color=INK,
            )

    fixed = summary.iloc[:4]
    x_fixed = np.arange(len(fixed))
    axes[1].bar(x_fixed, fixed["target_count"], color=TEAL, edgecolor=INK, linewidth=0.7, label="Target")
    axes[1].bar(
        x_fixed,
        fixed["timeout_count"],
        bottom=fixed["target_count"],
        color=ORANGE_LIGHT,
        edgecolor=INK,
        linewidth=0.7,
        hatch="//",
        label="Timeout",
    )
    axes[1].bar(
        x_fixed,
        fixed["stop_count"],
        bottom=fixed["target_count"] + fixed["timeout_count"],
        color=ORANGE,
        edgecolor=INK,
        linewidth=0.7,
        label="Stop",
    )
    axes[1].set_xticks(x_fixed, fixed["arm_label"])
    axes[1].set_ylim(0, 53)
    axes[1].set_ylabel("Trades (n=49)")
    axes[1].set_title("Higher targets trade hit rate for right-tail size", loc="left", weight="bold")
    axes[1].legend(frameon=False, ncol=3, loc="upper center")
    for position, hits in zip(x_fixed, fixed["target_count"]):
        axes[1].text(position, hits / 2, str(int(hits)), ha="center", va="center", color="white", weight="bold")
    for axis in axes:
        axis.grid(axis="y", color=GRID, linewidth=0.8, alpha=0.8)
        axis.set_axisbelow(True)
        axis.spines[["top", "right"]].set_visible(False)
        axis.tick_params(colors=INK)
    figure.suptitle(
        "BTCUSDT.P 1h · exit-only diagnostic · 12 bars · 20bp round trip",
        x=0.01,
        ha="left",
        fontsize=15,
        weight="bold",
        color=INK,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=170, bbox_inches="tight", facecolor="white")
    plt.close(figure)


def _clean_json(value: Any) -> Any:
    """Convert non-finite Python and NumPy floats to strict JSON nulls."""

    if isinstance(value, dict):
        return {str(key): _clean_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean_json(item) for item in value]
    if isinstance(value, (float, np.floating)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def run() -> dict[str, Any]:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    _verify_inputs(protocol)
    config = load_config()
    raw, _ = load_hourly_source(SOURCE, config)
    featured = add_features(raw)
    events = pd.read_csv(EVENTS, parse_dates=["entry_time"])
    controls = pd.read_csv(CONTROLS, parse_dates=["control_entry_time"])
    candidate_subjects, control_subjects = _subjects(events, controls, featured)
    horizon = int(protocol["fixed_inputs"]["horizon_bars"])
    cost = float(protocol["fixed_inputs"]["round_trip_cost_fraction"])
    candidate_trades = _simulate_arms(
        featured,
        candidate_subjects,
        subject_type="candidate",
        horizon=horizon,
        cost=cost,
    )
    control_trades = _simulate_arms(
        featured,
        control_subjects,
        subject_type="control",
        horizon=horizon,
        cost=cost,
    )
    _assert_baseline_parity(candidate_trades, events, subject_type="candidate")
    _assert_baseline_parity(control_trades, controls, subject_type="control")
    trades = pd.concat([candidate_trades, control_trades], ignore_index=True)
    summary = _summaries(trades)
    comparisons = _comparisons(trades, protocol)
    periods = _periods(trades)
    continuation = _continuation(trades)
    continuation_counts = {
        arm: int(continuation[f"{arm}_exit_family"].eq("target").sum())
        for arm in ["fixed_4R", "fixed_5R", "fixed_6R"]
    }
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "experiment_id": config["experiment_id"],
        "amendment_id": protocol["amendment_id"],
        "configuration_holdout_use": protocol["owner_authorization"]["configuration_holdout_use"],
        "analysis_source_commit": "a27950c75c9b9a966fdbed2114c8f2782981568c",
        "posthoc": True,
        "promotable": False,
        "input_hashes": {
            "protocol": sha256_file(PROTOCOL),
            "source": sha256_file(SOURCE),
            "events": sha256_file(EVENTS),
            "controls": sha256_file(CONTROLS),
        },
        "counts": {
            "candidate_entries": len(candidate_subjects),
            "matched_control_entries": len(control_subjects),
            "arms": len(ARM_ORDER),
            "candidate_arm_rows": len(candidate_trades),
            "control_arm_rows": len(control_trades),
            "baseline_3r_targets": int(
                candidate_trades[
                    candidate_trades["arm"].eq("fixed_3R")
                    & candidate_trades["exit_family"].eq("target")
                ].shape[0]
            ),
            "baseline_3r_target_continuation": continuation_counts,
        },
        "summary": summary.to_dict("records"),
        "comparisons_vs_fixed_3r": comparisons.to_dict("records"),
        "periods": periods.to_dict("records"),
        "validation": {
            "fixed_3r_candidate_parity": True,
            "fixed_3r_matched_control_parity": True,
            "all_paths_complete_12_bars": True,
            "signal_or_stop_changes": False,
            "threshold_selection_allowed": False,
        },
    }
    clean_payload = _clean_json(payload)
    RESULTS.mkdir(parents=True, exist_ok=True)
    final_outputs = [
        TRADES_OUT,
        SUMMARY_OUT,
        COMPARISONS_OUT,
        PERIODS_OUT,
        CONTINUATION_OUT,
        CHART_OUT,
        JSON_OUT,
    ]
    with tempfile.TemporaryDirectory(prefix=".exit-target-", dir=RESULTS) as temporary:
        staging = Path(temporary)
        trades.to_csv(staging / TRADES_OUT.name, index=False)
        summary.to_csv(staging / SUMMARY_OUT.name, index=False)
        comparisons.to_csv(staging / COMPARISONS_OUT.name, index=False)
        periods.to_csv(staging / PERIODS_OUT.name, index=False)
        continuation.to_csv(staging / CONTINUATION_OUT.name, index=False)
        _plot(summary, staging / CHART_OUT.name)
        write_json(staging / JSON_OUT.name, clean_payload)
        for final_output in final_outputs:
            os.replace(staging / final_output.name, final_output)
    return clean_payload


def main() -> int:
    payload = run()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
