#!/usr/bin/env python3
"""Quantify capital-path uncertainty for ETH 15m research candidates.

The audit resamples circular four-week blocks of marked-equity returns from the
already-consumed final-preholdout period.  It compares V9 at 0.50%, 0.75%, and
1.00% stop-risk budgets, plus the post-selection V10 and V11 hypotheses at 1%.
Signals, barriers, cost, and feature gates are not changed.

This is a conditional path-risk diagnostic, not an OOS test or forecast: it
assumes the observed week blocks are exchangeable enough to be resampled and
cannot represent unseen regimes.  Data loading remains bounded before
2026-03-01 and refuses repository holdout rows.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.research_pine_eth_15m import (
    SPLITS,
    Variant,
    build_feature_frame,
    load_config,
    load_research_frame,
    simulate_period,
    summarize,
)


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
RESULTS = EXPERIMENT / "results"
CHARTS = RESULTS / "charts"
CSV_OUTPUT = RESULTS / "path_risk_bootstrap.csv"
JSON_OUTPUT = RESULTS / "path_risk_bootstrap.json"
N_RESAMPLES = 20_000
BLOCK_WEEKS = 4


def weekly_returns(marked: pd.DataFrame, *, start: pd.Timestamp) -> pd.Series:
    """Convert a marked 15-minute equity path into complete ordered week returns."""

    equity = (
        marked.sort_values("open_time")
        .drop_duplicates("open_time", keep="last")
        .set_index("open_time")["normalized_equity"]
        .astype(float)
    )
    weekly = equity.resample("W-SUN").last().dropna()
    baseline = pd.Series([1.0], index=[start - pd.Timedelta(nanoseconds=1)])
    augmented = pd.concat([baseline, weekly])
    returns = augmented.pct_change().iloc[1:]
    returns.name = "weekly_return"
    return returns


def circular_block_bootstrap(
    returns: np.ndarray,
    *,
    n_resamples: int,
    block_weeks: int,
    seed: int,
) -> dict[str, np.ndarray]:
    """Resample equal-length circular week blocks and return path statistics."""

    values = np.asarray(returns, dtype=float)
    if values.ndim != 1 or len(values) < block_weeks:
        raise ValueError("returns must be one-dimensional and at least one block long")
    if np.any(values <= -1.0):
        raise ValueError("weekly return at or below -100% cannot be compounded")
    blocks_needed = int(np.ceil(len(values) / block_weeks))
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, len(values), size=(n_resamples, blocks_needed))
    offsets = np.arange(block_weeks)
    indices = (starts[:, :, None] + offsets[None, None, :]) % len(values)
    sampled = values[indices.reshape(n_resamples, -1)[:, : len(values)]]
    equity = np.cumprod(1.0 + sampled, axis=1)
    running_peak = np.maximum.accumulate(np.column_stack([np.ones(n_resamples), equity]), axis=1)
    with_start = np.column_stack([np.ones(n_resamples), equity])
    drawdown = 1.0 - with_start / running_peak
    return {
        "terminal_return": equity[:, -1] - 1.0,
        "maximum_drawdown": drawdown.max(axis=1),
    }


def _quantiles(values: np.ndarray) -> dict[str, float]:
    return {
        "q05": float(np.quantile(values, 0.05)),
        "q25": float(np.quantile(values, 0.25)),
        "median": float(np.quantile(values, 0.50)),
        "q75": float(np.quantile(values, 0.75)),
        "q95": float(np.quantile(values, 0.95)),
    }


def longest_losing_streak(trades: pd.DataFrame) -> int:
    """Return the longest consecutive run of non-positive unit outcomes."""

    losing = trades["project_net_return"].le(0.0).to_numpy(dtype=bool)
    longest = current = 0
    for value in losing:
        current = current + 1 if value else 0
        longest = max(longest, current)
    return int(longest)


def write_chart(rows: pd.DataFrame) -> None:
    """Plot return and drawdown bootstrap intervals for compact report review."""

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = rows["label"].tolist()
    x = np.arange(len(labels))
    fig, axes = plt.subplots(1, 2, figsize=(11.2, 4.7))
    return_median = rows["return_median_percent"].to_numpy(dtype=float)
    return_low = rows["return_q05_percent"].to_numpy(dtype=float)
    return_high = rows["return_q95_percent"].to_numpy(dtype=float)
    axes[0].errorbar(
        x,
        return_median,
        yerr=np.vstack([return_median - return_low, return_high - return_median]),
        fmt="o",
        capsize=5,
        color="#2563EB",
    )
    axes[0].axhline(0.0, color="#475569", linewidth=1.0)
    axes[0].set_title("Terminal return: median and 5–95%")
    axes[0].set_ylabel("Percent")
    axes[1].bar(x, rows["drawdown_q95_percent"], color="#D97706", alpha=0.82)
    axes[1].plot(x, rows["drawdown_median_percent"], "o", color="#7C2D12", label="median")
    axes[1].set_title("Max drawdown: 95th percentile and median")
    axes[1].set_ylabel("Percent")
    axes[1].legend(frameon=False)
    for axis in axes:
        axis.set_xticks(x, labels, rotation=24, ha="right")
        axis.grid(axis="y", alpha=0.2)
    fig.suptitle("ETH 15m four-week block bootstrap — descriptive, not OOS")
    fig.tight_layout()
    CHARTS.mkdir(parents=True, exist_ok=True)
    fig.savefig(CHARTS / "path_risk_bootstrap.png", dpi=160)
    plt.close(fig)


def main() -> None:
    config = load_config()
    raw, quality = load_research_frame(config)
    frame = build_feature_frame(raw)
    period = SPLITS[-1]
    arms = (
        ("V9 risk 0.50%", Variant("v9_risk_0p5", "v9_long", "v9_short"), 0.50),
        ("V9 risk 0.75%", Variant("v9_risk_0p75", "v9_long", "v9_short"), 0.75),
        ("V9 risk 1.00%", Variant("v9_risk_1p0", "v9_long", "v9_short"), 1.00),
        (
            "V10 volume 1.00%",
            Variant("v10_volume_1p0", "v10_volume_long", "v10_volume_short"),
            1.00,
        ),
        (
            "V11 long-only 1.00%",
            Variant(
                "v11_long_1p0",
                "v9_long",
                "v9_short",
                entry_directions=(1,),
            ),
            1.00,
        ),
    )
    rows: list[dict[str, Any]] = []
    detailed: list[dict[str, Any]] = []
    for arm_index, (label, spec, risk_percent) in enumerate(arms):
        trades, marked = simulate_period(frame, spec, period, risk_percent=risk_percent)
        summary = summarize(
            trades,
            marked,
            variant=spec.name,
            period=period.name,
            risk_percent=risk_percent,
        )
        weekly = weekly_returns(marked, start=period.start)
        boot = circular_block_bootstrap(
            weekly.to_numpy(dtype=float),
            n_resamples=N_RESAMPLES,
            block_weeks=BLOCK_WEEKS,
            seed=20261000 + arm_index,
        )
        terminal = boot["terminal_return"]
        drawdown = boot["maximum_drawdown"]
        return_q = _quantiles(terminal)
        drawdown_q = _quantiles(drawdown)
        row = {
            "label": label,
            "variant": spec.name,
            "risk_percent": risk_percent,
            "trades": int(len(trades)),
            "weeks": int(len(weekly)),
            "actual_return_percent": float(summary["return_percent"]),
            "actual_drawdown_15m_percent": float(summary["max_drawdown_15m_percent"]),
            "return_q05_percent": return_q["q05"] * 100.0,
            "return_median_percent": return_q["median"] * 100.0,
            "return_q95_percent": return_q["q95"] * 100.0,
            "drawdown_median_percent": drawdown_q["median"] * 100.0,
            "drawdown_q95_percent": drawdown_q["q95"] * 100.0,
            "probability_negative_terminal": float(np.mean(terminal < 0.0)),
            "probability_drawdown_over_15pct": float(np.mean(drawdown > 0.15)),
            "probability_drawdown_over_20pct": float(np.mean(drawdown > 0.20)),
            "longest_actual_losing_streak": longest_losing_streak(trades),
        }
        rows.append(row)
        detailed.append(
            {
                **row,
                "terminal_return_quantiles": return_q,
                "maximum_drawdown_quantiles": drawdown_q,
            }
        )
    table = pd.DataFrame(rows)
    table.to_csv(CSV_OUTPUT, index=False)
    write_chart(table)
    payload = {
        "audit": "four-week circular block bootstrap of marked weekly equity",
        "status": "descriptive_consumed_final_only",
        "resamples": N_RESAMPLES,
        "block_weeks": BLOCK_WEEKS,
        "holdout_rows_read": int(quality["holdout_rows_read"]),
        "strategy_barrier_or_cost_changed": False,
        "arms": detailed,
        "interpretation": (
            "Lower risk scales the capital path but cannot repair unit expectancy. "
            "Bootstrap probabilities are conditional on observed final-preholdout week "
            "blocks and are not forecasts or statistical validation."
        ),
    }
    if quality["holdout_rows_read"] != 0:
        raise RuntimeError("path-risk audit must not read repository holdout")
    JSON_OUTPUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
