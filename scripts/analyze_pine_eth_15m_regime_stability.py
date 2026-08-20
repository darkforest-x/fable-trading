#!/usr/bin/env python3
"""Audit frozen V9 stability across nine chronological market blocks.

No parameter is selected here.  V9 is restarted in fixed half-year blocks
(plus the final two-month stub) from 2022-02 through 2026-02, with the same
barriers, 20 bp cost, and 1% comparison risk.  Each block carries three exact,
non-reused calendar/volatility matched controls.  Equal-block exhaustive
sign-flip tests avoid treating serially dependent trades as independent.

The 2025-2026 blocks are already-consumed final diagnostics; holdout is never
read.  The audit cannot promote or alter the locked candidate.
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any, Sequence

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from scripts.research_pine_eth_15m import (
    Period,
    Variant,
    build_feature_frame,
    build_matched_controls,
    concentration_diagnostics,
    load_config,
    pair_controls,
    simulate_period,
    summarize,
)
from yoyo.layers.l3_backtest.pine_allin_v7 import load_development_frame


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
RESULTS = EXPERIMENT / "results"
CHARTS = RESULTS / "charts"
SUMMARY_CSV = RESULTS / "regime_stability.csv"
CONTROLS_CSV = RESULTS / "regime_stability_controls.csv"
OUTPUT_JSON = RESULTS / "regime_stability.json"
CHART_OUTPUT = CHARTS / "v9_regime_stability.png"
SAFE_END = pd.Timestamp("2026-03-01T00:00:00Z")
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")

PERIODS = (
    Period("2022H1", pd.Timestamp("2022-02-01T00:00:00Z"), pd.Timestamp("2022-07-01T00:00:00Z")),
    Period("2022H2", pd.Timestamp("2022-07-01T00:00:00Z"), pd.Timestamp("2023-01-01T00:00:00Z")),
    Period("2023H1", pd.Timestamp("2023-01-01T00:00:00Z"), pd.Timestamp("2023-07-01T00:00:00Z")),
    Period("2023H2", pd.Timestamp("2023-07-01T00:00:00Z"), pd.Timestamp("2024-01-01T00:00:00Z")),
    Period("2024H1", pd.Timestamp("2024-01-01T00:00:00Z"), pd.Timestamp("2024-07-01T00:00:00Z")),
    Period("2024H2", pd.Timestamp("2024-07-01T00:00:00Z"), pd.Timestamp("2025-01-01T00:00:00Z")),
    Period("2025H1", pd.Timestamp("2025-01-01T00:00:00Z"), pd.Timestamp("2025-07-01T00:00:00Z")),
    Period("2025H2", pd.Timestamp("2025-07-01T00:00:00Z"), pd.Timestamp("2026-01-01T00:00:00Z")),
    Period("2026M1M2", pd.Timestamp("2026-01-01T00:00:00Z"), SAFE_END),
)


def exhaustive_signflip(values: Sequence[float]) -> dict[str, Any]:
    array = np.asarray(values, dtype=float)
    if array.size == 0 or not np.isfinite(array).all():
        raise ValueError("sign-flip values must be finite and non-empty")
    observed = float(array.mean())
    null = np.array(
        [
            float((array * np.asarray(signs, dtype=float)).mean())
            for signs in itertools.product((-1.0, 1.0), repeat=len(array))
        ],
        dtype=float,
    )
    return {
        "observed_equal_block_mean_bp": observed,
        "positive_blocks": int((array > 0.0).sum()),
        "blocks": int(len(array)),
        "exact_assignments": int(len(null)),
        "one_sided_p_value": float((null >= observed).mean()),
        "minimum_block_bp": float(array.min()),
        "median_block_bp": float(np.median(array)),
    }


def main() -> None:
    config = load_config()
    raw = load_development_frame(
        PROJECT / config["instrument"]["data_path"],
        safe_end=SAFE_END,
        holdout_start=HOLDOUT_START,
    )
    frame = build_feature_frame(raw)
    times = pd.to_datetime(frame["open_time"], utc=True)
    rows = []
    controls_all = []
    for period in PERIODS:
        trades, equity = simulate_period(
            frame,
            Variant("v9_regime_stability", "v9_long", "v9_short"),
            period,
            risk_percent=1.0,
        )
        summary = summarize(
            trades,
            equity,
            variant="V9",
            period=period.name,
            risk_percent=1.0,
        )
        controls = build_matched_controls(
            frame,
            trades,
            period,
            seed=f"pine-eth15m-regime-{period.name}-v1",
        )
        controls.insert(0, "regime_period", period.name)
        controls_all.append(controls)
        pairs = pair_controls(trades, controls)
        concentration = concentration_diagnostics(trades)
        rows.append(
            {
                "period": period.name,
                "start": period.start.isoformat(),
                "end_exclusive": period.end.isoformat(),
                "consumed_final_diagnostic": bool(period.start >= pd.Timestamp("2025-01-01T00:00:00Z")),
                "trades": int(len(trades)),
                "candidate_net_bp": float(trades["project_net_return"].mean() * 10_000.0),
                "control_net_bp": float(pairs["control_mean_project_net"].mean() * 10_000.0),
                "candidate_minus_control_bp": float(pairs["excess_return"].mean() * 10_000.0),
                "return_percent": summary["return_percent"],
                "maximum_drawdown_percent": summary["max_drawdown_15m_percent"],
                "positive_trades": concentration["positive_trades"],
                "mean_without_top1_bp": concentration["mean_without_top1_bp"],
                "controls": int(len(controls)),
                "minimum_controls_per_trade": int(controls.groupby("trade_id").size().min()),
                "duplicate_control_starts": int(controls["control_signal_i"].duplicated().sum()),
            }
        )

    table = pd.DataFrame(rows)
    controls_table = pd.concat(controls_all, ignore_index=True)
    absolute_test = exhaustive_signflip(table["candidate_net_bp"])
    excess_test = exhaustive_signflip(table["candidate_minus_control_bp"])
    table.to_csv(SUMMARY_CSV, index=False)
    controls_table.to_csv(CONTROLS_CSV, index=False)

    CHARTS.mkdir(parents=True, exist_ok=True)
    x = np.arange(len(table))
    width = 0.26
    fig, ax = plt.subplots(figsize=(11.2, 5.4))
    ax.bar(x - width, table["candidate_net_bp"], width, label="V9 net")
    ax.bar(x, table["control_net_bp"], width, label="matched control")
    ax.bar(x + width, table["candidate_minus_control_bp"], width, label="excess")
    ax.axhline(0.0, color="#222222", linewidth=1.0)
    ax.set_xticks(x, table["period"], rotation=30, ha="right")
    ax.set_ylabel("bp per trade")
    ax.set_title("Frozen V9: equal chronological block stability")
    ax.legend(frameon=False, ncols=3)
    fig.tight_layout()
    fig.savefig(CHART_OUTPUT, dpi=180)
    plt.close(fig)

    payload = {
        "audit": "frozen V9 chronological regime stability",
        "blocks": int(len(table)),
        "period": [PERIODS[0].start.isoformat(), PERIODS[-1].end.isoformat()],
        "holdout_rows_read": int(times.ge(HOLDOUT_START).sum()),
        "barrier_parameters_changed": False,
        "parameter_search_performed": False,
        "selection_or_promotion_allowed": False,
        "matched_controls_exact": bool(
            table["minimum_controls_per_trade"].eq(3).all()
            and table["duplicate_control_starts"].eq(0).all()
        ),
        "absolute_net_equal_block_test": absolute_test,
        "matched_excess_equal_block_test": excess_test,
        "positive_leave_top1_blocks": int(table["mean_without_top1_bp"].gt(0.0).sum()),
        "recent_failures": table.loc[
            table["candidate_net_bp"].lt(0.0),
            ["period", "candidate_net_bp", "candidate_minus_control_bp"],
        ].to_dict("records"),
        "chart": str(CHART_OUTPUT.relative_to(PROJECT)),
        "decision": (
            "V9 is positive in seven of nine chronological blocks, but equal-block exact "
            "p-values for absolute net and matched excess remain above 0.01. The 2025H1 "
            "and 2026M1M2 losses demonstrate real regime dependence; the aggregate result "
            "cannot be described as uniformly stable."
        ),
    }
    if payload["holdout_rows_read"] or not payload["matched_controls_exact"]:
        raise RuntimeError("regime stability safety/controls check failed")
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
