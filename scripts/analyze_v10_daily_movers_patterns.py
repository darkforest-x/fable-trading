"""Profile patterns in the existing v10 ten-day post-hoc mover report.

Sources:
- analysis/output/v10_daily_movers_10d_report/signals.csv
- analysis/output/v10_daily_movers_10d_report/daily_rankings.csv

This script does not load K-lines or run a model.  It summarizes the already
materialized report at two grains: signal and symbol-day.  The daily Top20 was
selected with the completed day's absolute return, so all economic summaries
are descriptive of a look-ahead-selected review cohort, not a causal backtest.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT / "analysis/output/v10_daily_movers_10d_report"
DEFAULT_FACT_QUERY = (
    PROJECT
    / "analysis/output/v10_daily_movers_10d_patterns/prepare_report_facts.sql"
)
PROJECT_COST = 0.002


def safe_float(value: float) -> float | None:
    return None if not np.isfinite(value) else float(value)


def group_summary(frame: pd.DataFrame, key: str) -> list[dict]:
    rows: list[dict] = []
    for label, group in frame.groupby(key, observed=True, sort=False):
        rows.append(
            {
                key: str(label),
                "n": int(len(group)),
                "symbol_days": int(group["symbol_day"].nunique()),
                "mean_net_bps": safe_float(group["net_project"].mean() * 10_000),
                "median_net_bps": safe_float(group["net_project"].median() * 10_000),
                "positive_rate": safe_float((group["net_project"] > 0).mean()),
                "tp_rate": safe_float((group["outcome"] == "TP").mean()),
                "sl_rate": safe_float((group["outcome"] == "SL").mean()),
                "mean_conf": safe_float(group["conf"].mean()),
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--fact-query", type=Path, default=DEFAULT_FACT_QUERY)
    args = parser.parse_args()

    signals_raw = pd.read_csv(args.input / "signals.csv")
    ranked = pd.read_csv(args.input / "daily_rankings.csv")
    required_signals = {
        "day", "rank", "symbol", "daily_return", "signal_time", "conf",
        "status", "outcome", "gross", "net_taker", "held_bars",
    }
    required_ranked = {"day", "rank", "symbol", "return", "bars"}
    if missing := required_signals - set(signals_raw.columns):
        raise RuntimeError(f"signals.csv missing columns: {sorted(missing)}")
    if missing := required_ranked - set(ranked.columns):
        raise RuntimeError(f"daily_rankings.csv missing columns: {sorted(missing)}")

    fact_query = args.fact_query.read_text(encoding="utf-8")
    with sqlite3.connect(":memory:") as connection:
        signals_raw.to_sql("signals_raw", connection, index=False)
        signals = pd.read_sql_query(fact_query, connection)

    signals["day"] = pd.to_datetime(signals["day"], utc=True)
    signals["signal_time"] = pd.to_datetime(signals["signal_time"], utc=True)
    ranked["day"] = pd.to_datetime(ranked["day"], utc=True)
    signals["symbol_day"] = signals["day"].dt.strftime("%Y-%m-%d") + "|" + signals["symbol"]
    ranked["symbol_day"] = ranked["day"].dt.strftime("%Y-%m-%d") + "|" + ranked["symbol"]
    if signals.duplicated(["symbol", "signal_time"]).any():
        raise RuntimeError("duplicate symbol/signal_time rows")
    if ranked.duplicated(["day", "symbol"]).any():
        raise RuntimeError("duplicate day/symbol ranking rows")

    if not np.allclose(signals["net_project"], signals["gross"] - PROJECT_COST):
        raise RuntimeError("SQL net_project does not match the approved 20bp project cost")
    signals["day_direction"] = np.where(signals["daily_return"] < 0, "全天收跌", "全天收涨")
    signals["rank_bucket"] = pd.cut(
        signals["rank"], bins=[0, 5, 10, 20], labels=["Top 1–5", "Rank 6–10", "Rank 11–20"]
    )
    signals["conf_bucket"] = pd.cut(
        signals["conf"], bins=[0.3, 0.4, 0.5, 0.6, 0.7, 1.01],
        labels=["0.30–0.40", "0.40–0.50", "0.50–0.60", "0.60–0.70", "0.70+"],
        include_lowest=True,
    )
    signals["hour_bucket"] = pd.cut(
        signals["signal_time"].dt.hour,
        bins=[-1, 5, 11, 17, 23],
        labels=["00–05 UTC", "06–11 UTC", "12–17 UTC", "18–23 UTC"],
    )

    closed = signals[signals["status"] == "closed"].copy()
    running = signals[signals["status"] != "closed"].copy()
    if closed.empty:
        raise RuntimeError("no closed signals")

    first = (
        closed.sort_values("signal_time")
        .drop_duplicates("symbol_day", keep="first")
        .copy()
    )
    repeat_counts = signals.groupby("symbol_day").size().rename("signal_count")
    ranked_profile = ranked.join(repeat_counts, on="symbol_day").fillna({"signal_count": 0})
    ranked_profile["day_direction"] = np.where(ranked_profile["return"] < 0, "全天收跌", "全天收涨")

    positives = closed.loc[closed["net_project"] > 0, "net_project"].sort_values(ascending=False)
    negatives = closed.loc[closed["net_project"] <= 0, "net_project"]
    top5_sum = float(positives.head(5).sum())
    positive_sum = float(positives.sum())
    total_sum = float(closed["net_project"].sum())
    trimmed = closed.drop(index=positives.head(5).index)

    coverage_rows: list[dict] = []
    for direction, group in ranked_profile.groupby("day_direction", sort=False):
        coverage_rows.append(
            {
                "day_direction": str(direction),
                "ranked_symbol_days": int(len(group)),
                "signaled_symbol_days": int((group["signal_count"] > 0).sum()),
                "coverage_rate": float((group["signal_count"] > 0).mean()),
                "signals": int(group["signal_count"].sum()),
                "signals_per_ranked_symbol_day": float(group["signal_count"].mean()),
            }
        )

    day_rows: list[dict] = []
    for day, group in closed.groupby(closed["day"].dt.strftime("%m-%d"), sort=True):
        all_for_day = signals[signals["day"].dt.strftime("%m-%d") == day]
        day_rows.append(
            {
                "day": day,
                "signals": int(len(all_for_day)),
                "closed": int(len(group)),
                "mean_net_bps": float(group["net_project"].mean() * 10_000),
                "median_net_bps": float(group["net_project"].median() * 10_000),
                "positive_rate": float((group["net_project"] > 0).mean()),
                "down_day_signal_share": float((group["daily_return"] < 0).mean()),
            }
        )

    repeats: list[dict] = []
    repeat_table = repeat_counts.to_frame()
    repeat_table["bucket"] = np.where(
        repeat_table["signal_count"] == 1, "1 次",
        np.where(repeat_table["signal_count"] == 2, "2 次", "3+ 次"),
    )
    for bucket, group in repeat_table.groupby("bucket", sort=False):
        ids = set(group.index)
        subset = closed[closed["symbol_day"].isin(ids)]
        repeats.append(
            {
                "repeat_bucket": str(bucket),
                "symbol_days": int(len(group)),
                "signals": int(len(subset)),
                "mean_net_bps": float(subset["net_project"].mean() * 10_000),
                "positive_rate": float((subset["net_project"] > 0).mean()),
            }
        )

    top_trades = closed.nlargest(10, "net_project")[
        ["day", "symbol", "rank", "daily_return", "signal_time", "conf", "outcome", "net_project"]
    ].copy()
    top_trades["day"] = top_trades["day"].dt.strftime("%m-%d")
    top_trades["signal_time"] = top_trades["signal_time"].dt.strftime("%H:%M")
    top_trades["daily_return_pct"] = top_trades.pop("daily_return") * 100
    top_trades["net_bps"] = top_trades.pop("net_project") * 10_000

    outcome_rows: list[dict] = []
    for outcome, group in closed.groupby("outcome", sort=False):
        outcome_rows.append(
            {
                "outcome": str(outcome),
                "n": int(len(group)),
                "share": float(len(group) / len(closed)),
                "mean_net_bps": float(group["net_project"].mean() * 10_000),
                "median_held_bars": float(group["held_bars"].median()),
            }
        )

    result = {
        "scope": {
            "days": [signals["day"].min().isoformat(), signals["day"].max().isoformat()],
            "ranked_symbol_days": int(len(ranked)),
            "signals": int(len(signals)),
            "closed": int(len(closed)),
            "running": int(len(running)),
            "signaled_symbol_days": int(signals["symbol_day"].nunique()),
            "unique_symbols": int(signals["symbol"].nunique()),
            "cost_existing_bps": 10,
            "cost_project_bps": 20,
        },
        "aggregate_closed": {
            "mean_gross_bps": float(closed["gross"].mean() * 10_000),
            "mean_net_10bp_bps": float(closed["net_taker"].mean() * 10_000),
            "mean_net_20bp_bps": float(closed["net_project"].mean() * 10_000),
            "median_net_20bp_bps": float(closed["net_project"].median() * 10_000),
            "positive_rate_20bp": float((closed["net_project"] > 0).mean()),
            "profit_factor_20bp": float(positives.sum() / abs(negatives.sum())),
            "sum_net_20bp": total_sum,
            "top5_positive_share": top5_sum / positive_sum,
            "mean_without_top5_bps": float(trimmed["net_project"].mean() * 10_000),
            "median_held_bars": float(closed["held_bars"].median()),
            "spearman_conf_net": safe_float(closed["conf"].corr(closed["net_project"], method="spearman")),
            "first_signal_spearman_conf_net": safe_float(
                first["conf"].corr(first["net_project"], method="spearman")
            ),
            "spearman_daily_return_net": safe_float(closed["daily_return"].corr(closed["net_project"], method="spearman")),
        },
        "coverage_by_direction": coverage_rows,
        "all_signals_by_direction": group_summary(closed, "day_direction"),
        "first_signal_by_direction": group_summary(first, "day_direction"),
        "by_confidence": group_summary(closed, "conf_bucket"),
        "by_rank": group_summary(closed, "rank_bucket"),
        "by_hour": group_summary(closed, "hour_bucket"),
        "down_day_by_hour": group_summary(
            closed[closed["day_direction"] == "全天收跌"], "hour_bucket"
        ),
        "first_signal_by_hour": group_summary(first, "hour_bucket"),
        "first_signal_by_confidence": group_summary(first, "conf_bucket"),
        "by_outcome": outcome_rows,
        "by_repeat_count": repeats,
        "by_day": day_rows,
        "top_trades": top_trades.to_dict(orient="records"),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
