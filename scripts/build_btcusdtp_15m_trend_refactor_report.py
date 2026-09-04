#!/usr/bin/env python3
"""Build the BTCUSDT.P 15m trend-runner failure-analysis artifacts.

Inputs are frozen experiment outputs plus the physically pre-holdout direct
15-minute source.  Entry features use the completed signal bar or earlier.
The only forward reads in this script resolve exits and outcome diagnostics.
The repository holdout beginning 2026-05-04 is never opened.

The script distinguishes three contracts that should not be conflated:
high-recall display signals, economically qualified entries, and trend-following
exits.  It also corrects the diagnostic meaning of giveback: exit giveback is
MFE observed through the exit candle minus realized ATR, while the older
``horizon_mfe - realized`` field is only a post-exit opportunity gap.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Rectangle

from scripts.backtest_two_key_candle_pine_v8_btc_1h import signflip_p
from scripts.research_btcusdtp_15m_ma_runner_grid import (
    BAR_DELTA,
    add_dual_references,
    add_exit_references,
    fixed_reference,
    fresh_events,
    load_config,
    load_fresh_frame,
    matched_controls,
    simulate_policy,
)

ROOT = Path(__file__).resolve().parents[1]
V3 = ROOT / "experiments/active/exp-btcusdtp-15m-high-recall-l2-trend-runner-preholdout-20260904-v1"
V4 = ROOT / "experiments/active/exp-btcusdtp-15m-ma-runner-grid-preholdout-20260904-v1"
OUTPUT = ROOT / "analysis/output/btcusdtp_15m_trend_refactor_20260904"
CURRENT_PINE_PARAMS = {
    "trend_ma": "SMA60",
    "exit_style": "trail",
    "arm_atr": 2.0,
    "buffer_atr": 1.0,
    "max_hold_bars": 96,
    "leg_mix": "full",
}
COLORS = {"teal": "#17A297", "orange": "#F59E0B", "red": "#E85D75", "ink": "#26323A"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    compression: str | dict[str, Any] | None = None
    if path.suffix == ".gz":
        compression = {"method": "gzip", "mtime": 0}
    frame.to_csv(path, index=False, compression=compression)


def profit_factor(returns: pd.Series) -> float:
    positive = float(returns[returns > 0].sum())
    negative = float(-returns[returns < 0].sum())
    return positive / negative if negative > 0 else np.inf


def summary_metrics(trades: pd.DataFrame) -> dict[str, Any]:
    net = trades["net_return"].astype(float)
    armed = trades[trades["runner_armed"].astype(bool)]
    return {
        "events": len(trades),
        "mean_gross_bp": float(trades["gross_return"].mean() * 1e4),
        "mean_net_bp": float(net.mean() * 1e4),
        "median_net_bp": float(net.median() * 1e4),
        "win_rate": float(net.gt(0).mean()),
        "profit_factor": profit_factor(net),
        "p95_net_bp": float(net.quantile(0.95) * 1e4),
        "p99_net_bp": float(net.quantile(0.99) * 1e4),
        "max_net_bp": float(net.max() * 1e4),
        "median_hold_bars": float(trades["hold_bars"].median()),
        "armed_events": len(armed),
        "armed_rate": float(len(armed) / len(trades)) if len(trades) else np.nan,
        "armed_net_loss_rate": float(armed["net_return"].le(0).mean()) if len(armed) else np.nan,
        "mean_exit_giveback_atr": float(trades["exit_giveback_atr"].mean()),
    }


def _stop_fill(open_price: float, stop: float, direction: int) -> float:
    if direction > 0 and open_price < stop:
        return open_price
    if direction < 0 and open_price > stop:
        return open_price
    return stop


def _floor_price(
    name: str,
    *,
    entry: float,
    direction: int,
    signal_atr: float,
    cost: float,
) -> float | None:
    if name == "none":
        return None
    if name == "break_even":
        return entry
    if name == "fee_cover":
        return entry * (1.0 + direction * cost)
    if name == "plus_0.5atr":
        return entry + direction * 0.5 * signal_atr
    if name == "plus_1atr":
        return entry + direction * signal_atr
    raise ValueError(f"unknown floor: {name}")


def simulate_current_pine_with_floor(
    frame: pd.DataFrame,
    events: pd.DataFrame,
    config: Mapping[str, Any],
    floor_name: str,
) -> pd.DataFrame:
    """Resolve the saved Pine SMA60 runner with an optional post-arm floor.

    The stop checked on bar ``t`` was computed no later than completed bar
    ``t-1``.  A +2 ATR close arms the SMA60-minus-1ATR trail and optional floor
    for the following bar.  Candle-high/low MFE through the exit bar is an
    observed upper bound because intrabar order is unavailable.
    """

    arrays = {
        name: frame[name].to_numpy(dtype=float)
        for name in ("open", "high", "low", "close", "atr")
    }
    arrays["SMA60"] = frame["exit_SMA60"].to_numpy(dtype=float)
    cost = float(config["entry"]["round_trip_cost_fraction"])
    stop_atr = float(config["entry"]["initial_stop_atr"])
    rows: list[dict[str, Any]] = []
    for event in events.to_dict("records"):
        entry_i = int(event["entry_i"])
        direction = int(event["direction"])
        entry = float(event["entry_price"])
        signal_atr = float(event["signal_atr"])
        active_stop = entry - direction * stop_atr * signal_atr
        end_i = entry_i + int(CURRENT_PINE_PARAMS["max_hold_bars"]) - 1
        armed = False
        arm_i: int | None = None
        observed_mfe = 0.0
        outcome = "timeout"
        exit_i = end_i
        exit_price = float(arrays["close"][end_i])
        for i in range(entry_i, end_i + 1):
            favourable = (
                float(arrays["high"][i]) - entry
                if direction > 0
                else entry - float(arrays["low"][i])
            )
            observed_mfe = max(observed_mfe, favourable)
            hit_stop = (
                float(arrays["low"][i]) <= active_stop
                if direction > 0
                else float(arrays["high"][i]) >= active_stop
            )
            if hit_stop:
                exit_i = i
                exit_price = _stop_fill(float(arrays["open"][i]), active_stop, direction)
                outcome = "ma_or_floor_stop" if armed else "hard_stop"
                break
            close_profit_atr = direction * (float(arrays["close"][i]) - entry) / signal_atr
            if not armed and close_profit_atr >= float(CURRENT_PINE_PARAMS["arm_atr"]):
                armed = True
                arm_i = i
            if not armed or not np.isfinite(arrays["SMA60"][i]):
                continue
            candidate = float(arrays["SMA60"][i]) - direction * float(
                CURRENT_PINE_PARAMS["buffer_atr"]
            ) * float(arrays["atr"][i])
            floor = _floor_price(
                floor_name,
                entry=entry,
                direction=direction,
                signal_atr=signal_atr,
                cost=cost,
            )
            if floor is not None:
                candidate = max(candidate, floor) if direction > 0 else min(candidate, floor)
            active_stop = max(active_stop, candidate) if direction > 0 else min(active_stop, candidate)
        gross = direction * (exit_price / entry - 1.0)
        realized_atr = gross * entry / signal_atr
        mfe_at_exit_atr = observed_mfe / signal_atr
        rows.append(
            {
                **event,
                "exit_i": exit_i,
                "exit_time": frame.loc[exit_i, "open_time"] + BAR_DELTA,
                "outcome": outcome,
                "hold_bars": exit_i - entry_i + 1,
                "gross_return": gross,
                "net_return": gross - cost,
                "runner_armed": armed,
                "runner_arm_i": arm_i,
                "mfe_at_exit_atr": mfe_at_exit_atr,
                "realized_atr": realized_atr,
                "exit_giveback_atr": mfe_at_exit_atr - realized_atr,
                "profit_floor": floor_name,
            }
        )
    return pd.DataFrame(rows)


def add_corrected_giveback(trades: pd.DataFrame) -> pd.DataFrame:
    out = trades.copy()
    out["horizon_opportunity_gap_atr"] = out["horizon_mfe_atr"] - out["realized_atr"]
    out["exit_giveback_atr"] = out["mfe_at_exit_atr"] - out["realized_atr"]
    return out


def failure_mechanics(trades: pd.DataFrame) -> pd.DataFrame:
    armed = trades["runner_armed"].astype(bool)
    net = trades["net_return"].astype(float)
    gross = trades["gross_return"].astype(float)
    large_giveback = trades["exit_giveback_atr"].ge(2.0)
    category = np.select(
        [
            ~armed & net.le(0),
            ~armed & net.gt(0),
            armed & gross.gt(0) & net.le(0),
            armed & net.le(0),
            armed & net.gt(0) & large_giveback,
            armed & net.gt(0),
        ],
        [
            "failed_before_activation",
            "small_win_unarmed",
            "cost_erased_after_arm",
            "armed_then_reversed_to_loss",
            "armed_winner_large_giveback",
            "armed_winner_retained",
        ],
        default="unclassified",
    )
    work = trades.assign(failure_category=category)
    grouped = (
        work.groupby("failure_category", as_index=False)
        .agg(
            events=("setup_id", "size"),
            mean_net_bp=("net_return", lambda x: float(x.mean() * 1e4)),
            cumulative_net_bp=("net_return", lambda x: float(x.sum() * 1e4)),
            mean_mfe_at_exit_atr=("mfe_at_exit_atr", "mean"),
            mean_exit_giveback_atr=("exit_giveback_atr", "mean"),
        )
        .sort_values("cumulative_net_bp")
    )
    return grouped


def group_metrics(trades: pd.DataFrame, column: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for value, part in trades.groupby(column, sort=True):
        row = {column: value, **summary_metrics(part)}
        rows.append(row)
    return pd.DataFrame(rows).sort_values("mean_net_bp", ascending=False)


def feature_quartiles(trades: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for feature in features:
        usable = trades[trades[feature].notna()].copy()
        usable["quartile"] = pd.qcut(
            usable[feature].rank(method="first"), 4, labels=["Q1", "Q2", "Q3", "Q4"]
        )
        for quartile, part in usable.groupby("quartile", observed=True, sort=True):
            rows.append(
                {
                    "feature": feature,
                    "quartile": str(quartile),
                    "events": len(part),
                    "feature_min": float(part[feature].min()),
                    "feature_max": float(part[feature].max()),
                    "mean_net_bp": float(part["net_return"].mean() * 1e4),
                    "win_rate": float(part["net_return"].gt(0).mean()),
                }
            )
    return pd.DataFrame(rows)


def temporal_metrics(development: pd.DataFrame, fresh: pd.DataFrame) -> pd.DataFrame:
    def label(stamp: pd.Timestamp) -> str:
        stamp = pd.Timestamp(stamp)
        if stamp.year == 2026 and stamp.month <= 2:
            return "2026P1"
        return f"{stamp.year}H{1 if stamp.month <= 6 else 2}"

    rows: list[dict[str, Any]] = []
    dev = development.assign(time_block=development["entry_time"].map(label))
    for block, part in dev.groupby("time_block", sort=True):
        rows.append({"time_block": block, "role": "development", **summary_metrics(part)})
    fresh_labeled = fresh.assign(time_block=fresh["entry_time"].dt.strftime("%Y-%m"))
    for block, part in fresh_labeled.groupby("time_block", sort=True):
        rows.append({"time_block": block, "role": "fresh_preholdout_validation_posthoc", **summary_metrics(part)})
    return pd.DataFrame(rows)


def binary_auc(labels: pd.Series, scores: pd.Series) -> float:
    labels_array = labels.astype(bool).to_numpy()
    score_ranks = scores.astype(float).rank(method="average").to_numpy()
    positives = int(labels_array.sum())
    negatives = int((~labels_array).sum())
    if positives == 0 or negatives == 0:
        return np.nan
    rank_sum = float(score_ranks[labels_array].sum())
    return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def l2_diagnostics() -> dict[str, Any]:
    pool = pd.read_csv(V3 / "results/validation_scored_pool.csv.gz")
    selected = pd.read_csv(V3 / "results/validation_l2_selected.csv.gz")
    summary = json.loads((V3 / "results/validation_summary.json").read_text(encoding="utf-8"))
    positive = pool["net_return"].gt(0)
    n_top = max(1, int(np.ceil(len(pool) * 0.10)))
    model_top = pool.nlargest(n_top, "l2_score")
    native_top = pool.nlargest(n_top, "signal_score")
    return {
        "pool_events": len(pool),
        "pool_net_positive_rate": float(positive.mean()),
        "validation_auc_net_positive": binary_auc(positive, pool["l2_score"]),
        "top_decile_events": n_top,
        "model_top_decile_mean_net_bp": float(model_top["net_return"].mean() * 1e4),
        "model_top_decile_win_rate": float(model_top["net_return"].gt(0).mean()),
        "single_feature_signal_score_top_decile_mean_net_bp": float(
            native_top["net_return"].mean() * 1e4
        ),
        "single_feature_signal_score_top_decile_win_rate": float(
            native_top["net_return"].gt(0).mean()
        ),
        "frozen_gate_events": len(selected),
        "frozen_gate_mean_net_bp": float(selected["net_return"].mean() * 1e4),
        "score_permutation_p_one_sided": float(
            summary["metrics"]["score_permutation_p_one_sided"]
        ),
        "matched_control_excess_bp": float(summary["metrics"]["matched_control_excess_bp"]),
        "matched_control_p_one_sided": float(
            summary["metrics"]["paired_control_signflip_p_one_sided"]
        ),
    }


def plot_exit_comparison(comparison: pd.DataFrame, path: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4))
    labels = comparison["policy"].tolist()
    colors = [COLORS["orange"], COLORS["teal"], "#4F79D8"]
    axes[0].bar(labels, comparison["mean_net_bp"], color=colors)
    axes[0].axhline(0, color=COLORS["ink"], linewidth=0.8)
    axes[0].set_ylabel("Mean net return (bp/trade)")
    axes[0].set_title("Fresh pre-holdout expectancy")
    axes[0].tick_params(axis="x", rotation=18)
    x = np.arange(len(labels))
    width = 0.36
    axes[1].bar(x - width / 2, comparison["p99_net_bp"], width, label="p99", color="#77BDB5")
    axes[1].bar(x + width / 2, comparison["max_net_bp"], width, label="max", color=COLORS["teal"])
    axes[1].set_xticks(x, labels, rotation=18)
    axes[1].set_ylabel("Net return (bp)")
    axes[1].set_title("Right-tail capture")
    axes[1].legend(frameon=False)
    for axis in axes:
        axis.grid(axis="y", alpha=0.18)
    fig.tight_layout()
    fig.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def plot_failures(failures: pd.DataFrame, path: Path) -> None:
    ordered = failures.sort_values("cumulative_net_bp")
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.0))
    event_colors = [COLORS["red"] if value < 0 else COLORS["teal"] for value in ordered["mean_net_bp"]]
    axes[0].barh(ordered["failure_category"], ordered["events"], color=event_colors)
    axes[0].set_xlabel("Trades")
    axes[0].set_title("What happened")
    contribution_colors = [COLORS["red"] if value < 0 else COLORS["teal"] for value in ordered["cumulative_net_bp"]]
    axes[1].barh(ordered["failure_category"], ordered["cumulative_net_bp"], color=contribution_colors)
    axes[1].axvline(0, color=COLORS["ink"], linewidth=0.8)
    axes[1].set_xlabel("Cumulative net return (bp, unweighted sum)")
    axes[1].set_title("Where expectancy was lost or earned")
    for axis in axes:
        axis.grid(axis="x", alpha=0.18)
    fig.tight_layout()
    fig.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def plot_floor_tradeoff(floors: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 4.8))
    x = np.arange(len(floors))
    bars = ax.bar(x, floors["mean_net_bp"], color=COLORS["teal"], alpha=0.88)
    ax.set_xticks(x, floors["profit_floor"], rotation=18)
    ax.set_ylabel("Mean net return (bp/trade)")
    ax.axhline(0, color=COLORS["ink"], linewidth=0.8)
    ax2 = ax.twinx()
    ax2.plot(x, floors["armed_net_loss_rate"] * 100, marker="o", color=COLORS["orange"], label="Armed trades ending net-negative")
    ax2.set_ylabel("Armed net-loss rate (%)")
    ax.set_title("Profit floors reduce reversals but cut expectancy")
    for bar, value in zip(bars, floors["mean_net_bp"]):
        ax.text(bar.get_x() + bar.get_width() / 2, value - 0.35, f"{value:.1f}", ha="center", va="top", color="white", fontsize=8)
    ax.grid(axis="y", alpha=0.18)
    fig.tight_layout()
    fig.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def active_stop_path(frame: pd.DataFrame, trade: Mapping[str, Any]) -> pd.DataFrame:
    entry_i = int(trade["entry_i"])
    exit_i = int(trade["exit_i"])
    direction = int(trade["direction"])
    entry = float(trade["entry_price"])
    signal_atr = float(trade["signal_atr"])
    active_stop = entry - direction * 2.0 * signal_atr
    armed = False
    rows: list[dict[str, Any]] = []
    for i in range(entry_i, exit_i + 1):
        rows.append({"i": i, "active_stop": active_stop, "armed_before_bar": armed})
        hit = (
            float(frame.loc[i, "low"]) <= active_stop
            if direction > 0
            else float(frame.loc[i, "high"]) >= active_stop
        )
        if hit:
            break
        close_profit_atr = direction * (float(frame.loc[i, "close"]) - entry) / signal_atr
        if not armed and close_profit_atr >= 2.0:
            armed = True
        if armed:
            candidate = float(frame.loc[i, "exit_SMA60"]) - direction * float(
                frame.loc[i, "atr"]
            )
            active_stop = max(active_stop, candidate) if direction > 0 else min(active_stop, candidate)
    return pd.DataFrame(rows)


def _draw_trade_panel(
    ax: plt.Axes,
    frame: pd.DataFrame,
    trade: Mapping[str, Any],
    title: str,
) -> None:
    entry_i = int(trade["entry_i"])
    exit_i = int(trade["exit_i"])
    start_i = max(0, entry_i - 20)
    end_i = min(len(frame) - 1, exit_i + 12)
    view = frame.loc[start_i:end_i].copy()
    view["x"] = np.arange(len(view))
    for row in view.itertuples(index=False):
        colour = COLORS["teal"] if (row.high + row.low) / 2 >= row.exit_EMA30 else COLORS["orange"]
        x = float(row.x)
        ax.vlines(x, row.low, row.high, color=colour, linewidth=0.8, alpha=0.9)
        bottom = min(row.open, row.close)
        height = max(abs(row.close - row.open), max(row.high - row.low, 1e-9) * 0.012)
        ax.add_patch(Rectangle((x - 0.32, bottom), 0.64, height, facecolor=colour, edgecolor=colour, linewidth=0.7))
    x_values = view["x"].to_numpy()
    ema_values = view["exit_EMA30"].to_numpy(dtype=float)
    side = ((view["high"] + view["low"]) / 2 >= view["exit_EMA30"]).to_numpy()
    for index in range(len(view) - 1):
        colour = COLORS["teal"] if side[index] else COLORS["orange"]
        ax.plot(x_values[index : index + 2], ema_values[index : index + 2], color=colour, linewidth=1.0)
    ax.plot(x_values, view["exit_SMA60"], color="#597E8C", linewidth=1.15, alpha=0.82, label="SMA60 runner")
    path = active_stop_path(frame, trade)
    path = path[path["i"].between(start_i, end_i)].copy()
    path["x"] = path["i"] - start_i
    pre = path[~path["armed_before_bar"]]
    post = path[path["armed_before_bar"]]
    ax.step(pre["x"], pre["active_stop"], where="post", color=COLORS["red"], linewidth=1.2, label="active stop")
    ax.step(post["x"], post["active_stop"], where="post", color=COLORS["teal"], linewidth=1.5, label="MA trail")
    entry_x = entry_i - start_i
    exit_x = exit_i - start_i
    ax.axhline(float(trade["entry_price"]), color=COLORS["ink"], linewidth=0.8, linestyle=":", alpha=0.7)
    ax.scatter([entry_x], [trade["entry_price"]], marker="^" if int(trade["direction"]) > 0 else "v", color=COLORS["teal"], s=42, zorder=5)
    ax.scatter([exit_x], [frame.loc[exit_i, "close"]], marker="x", color=COLORS["red"], s=42, zorder=5)
    arm_i = trade.get("runner_arm_i")
    if pd.notna(arm_i):
        arm_x = int(float(arm_i)) - start_i
        ax.axvline(arm_x, color=COLORS["teal"], linewidth=0.8, linestyle="--", alpha=0.7)
        ax.text(arm_x, ax.get_ylim()[1], " +2ATR armed", va="top", ha="left", fontsize=8, color=COLORS["teal"])
    tick_positions = np.linspace(0, len(view) - 1, min(6, len(view)), dtype=int)
    tick_labels = view.iloc[tick_positions]["open_time"].dt.strftime("%m-%d\n%H:%M")
    ax.set_xticks(tick_positions, tick_labels)
    ax.set_title(title, loc="left")
    ax.grid(alpha=0.14)


def plot_trade_paths(frame: pd.DataFrame, trades: pd.DataFrame, path: Path) -> None:
    best = trades.loc[trades["net_return"].idxmax()]
    early = trades[~trades["runner_armed"].astype(bool) & trades["net_return"].le(0)].copy()
    median_loss = float(early["net_return"].median())
    typical = early.loc[(early["net_return"] - median_loss).abs().idxmin()]
    fig, axes = plt.subplots(2, 1, figsize=(12, 8.5))
    _draw_trade_panel(
        axes[0],
        frame,
        best,
        f"Trend captured: {pd.Timestamp(best['entry_time']).strftime('%Y-%m-%d %H:%MZ')} · {best['net_return'] * 1e4:+.1f}bp · {int(best['hold_bars'])} bars",
    )
    _draw_trade_panel(
        axes[1],
        frame,
        typical,
        f"Typical pre-arm failure: {pd.Timestamp(typical['entry_time']).strftime('%Y-%m-%d %H:%MZ')} · {typical['net_return'] * 1e4:+.1f}bp · {int(typical['hold_bars'])} bars",
    )
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper right", frameon=False, ncol=3)
    fig.suptitle("Saved Pine SMA60 runner: right-tail benefit versus entry failure", x=0.07, ha="left", fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    config = load_config()
    selection = json.loads((V4 / "results/selection_receipt.json").read_text(encoding="utf-8"))
    fresh, source = load_fresh_frame(config)
    frame = add_dual_references(fresh, "EMA30", "SMA60")
    frame = add_exit_references(frame)
    events = fresh_events(fresh, config, selection)

    current_raw = simulate_policy(frame, events, CURRENT_PINE_PARAMS, config)
    current = add_corrected_giveback(current_raw)
    locked = add_corrected_giveback(
        simulate_policy(frame, events, selection["selected_params"], config)
    )
    fixed = add_corrected_giveback(fixed_reference(frame, events, config))
    no_floor = simulate_current_pine_with_floor(frame, events, config, "none")
    np.testing.assert_allclose(
        no_floor["net_return"], current["net_return"], rtol=0.0, atol=1e-12
    )
    if not no_floor["exit_i"].equals(current["exit_i"]):
        raise RuntimeError("saved-Pine simulator exit index drift")

    floor_rows: list[dict[str, Any]] = []
    for floor_name in ("none", "break_even", "fee_cover", "plus_0.5atr", "plus_1atr"):
        floor_trades = simulate_current_pine_with_floor(frame, events, config, floor_name)
        write_csv(floor_trades, OUTPUT / f"fresh_profit_floor_{floor_name}_trades.csv.gz")
        floor_rows.append({"profit_floor": floor_name, **summary_metrics(floor_trades)})
    floors = pd.DataFrame(floor_rows)

    comparison = pd.DataFrame(
        [
            {"policy": "Fixed +5ATR", **summary_metrics(fixed)},
            {"policy": "Grid locked EMA30", **summary_metrics(locked)},
            {"policy": "Saved Pine SMA60", **summary_metrics(current)},
        ]
    )
    failures = failure_mechanics(current)
    side = current.assign(side=np.where(current["direction"].gt(0), "long", "short"))
    side_table = group_metrics(side, "side")
    family_table = group_metrics(current, "signal_family")
    development_current = add_corrected_giveback(
        pd.read_csv(V4 / "results/development_initial_trades.csv.gz", parse_dates=["entry_time", "exit_time"])
    )
    temporal = temporal_metrics(development_current, current)
    family_stability = pd.concat(
        [
            group_metrics(development_current, "signal_family").assign(period="development_2024_to_2026-02"),
            family_table.assign(period="fresh_2026-03_to_2026-05-03"),
        ],
        ignore_index=True,
    )
    quartiles = feature_quartiles(
        current,
        [
            "signal_score",
            "atr_ratio96",
            "adx14",
            "signed_body_atr",
            "sma60_sma160_spread_atr",
            "fee_to_risk",
        ],
    )
    l2 = l2_diagnostics()
    current_controls, current_control_pairs = matched_controls(
        frame, current, CURRENT_PINE_PARAMS, config
    )
    current_matched = current_control_pairs[
        current_control_pairs["match_status"].eq("matched_exact")
    ]
    current_excess = current_matched["paired_excess_return"].astype(float)
    current_control_audit = {
        "matched_events": len(current_matched),
        "mean_candidate_minus_control_bp": float(current_excess.mean() * 1e4),
        "trade_level_signflip_p_one_sided": float(
            signflip_p(current_excess, resamples=100_000, seed=20260908)
        ),
        "warning": "posthoc on an already opened pre-holdout validation slice; descriptive only",
    }

    write_csv(current, OUTPUT / "fresh_saved_pine_sma60_trades.csv.gz")
    write_csv(comparison, OUTPUT / "exit_policy_comparison.csv")
    write_csv(floors, OUTPUT / "profit_floor_comparison.csv")
    write_csv(failures, OUTPUT / "failure_mechanics.csv")
    write_csv(side_table, OUTPUT / "side_metrics.csv")
    write_csv(family_stability, OUTPUT / "family_stability.csv")
    write_csv(quartiles, OUTPUT / "feature_quartiles.csv")
    write_csv(temporal, OUTPUT / "temporal_stability.csv")
    write_csv(current_controls, OUTPUT / "saved_pine_matched_controls.csv.gz")
    write_csv(current_control_pairs, OUTPUT / "saved_pine_matched_control_pairs.csv")
    plot_exit_comparison(comparison, OUTPUT / "exit_policy_comparison.png")
    plot_failures(failures, OUTPUT / "failure_mechanics.png")
    plot_floor_tradeoff(floors, OUTPUT / "profit_floor_tradeoff.png")
    plot_trade_paths(frame, current, OUTPUT / "trade_path_examples.png")
    for name in (
        "shap_beeswarm.png",
        "shap_loser_winner_delta.png",
        "shap_highest_scored_loss.png",
    ):
        shutil.copyfile(V3 / "results/shap_audit" / name, OUTPUT / name)

    summary = {
        "phase": "post_validation_failure_analysis_complete",
        "decision": "reject_for_economic_use_keep_research_display_only",
        "source": source,
        "holdout_rows_read": 0,
        "saved_pine_params": CURRENT_PINE_PARAMS,
        "locked_grid_params": selection["selected_params"],
        "exit_policy_comparison": comparison.to_dict("records"),
        "profit_floor_comparison": floors.to_dict("records"),
        "failure_mechanics": failures.to_dict("records"),
        "l2_diagnostics": l2,
        "saved_pine_matched_control_audit": current_control_audit,
        "diagnostic_correction": {
            "exit_giveback_atr": "mfe_at_exit_atr - realized_atr",
            "horizon_opportunity_gap_atr": "horizon_mfe_atr - realized_atr",
            "warning": "OHLC cannot reveal intrabar order; exit-candle MFE is an observed upper bound.",
            "selection_impact": "none; the erroneous label was only a tertiary diagnostic and did not alter the frozen parameter path",
        },
        "artifacts": {},
    }
    for path in sorted(OUTPUT.iterdir()):
        if path.name != "summary.json":
            summary["artifacts"][path.name] = {"sha256": sha256(path), "bytes": path.stat().st_size}
    (OUTPUT / "summary.json").write_text(
        json.dumps(json_value(summary), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(json_value(summary), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
