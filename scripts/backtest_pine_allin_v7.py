#!/usr/bin/env python3
"""Run the preregistered ALLIN-V7 Pine replay on 54 causal 15m series.

The runner stops at 2026-03-01 UTC, 64 days before the repository holdout.  It
does not tune signal thresholds or barrier/cost assumptions.  Each directional
result is paired with deterministic random entries from the same symbol, month,
Hong Kong six-hour block, ATR quintile, side, holding horizon, barrier, and
20bp round-trip cost.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/backtest_pine_allin_v7.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from yoyo.evaluation.permutation import permutation_test
from yoyo.layers.l3_backtest.pine_allin_v7 import (
    Arm,
    EXPECTED_BAR,
    SignalParameters,
    add_indicators,
    auc_from_scores,
    deterministic_control_indices,
    load_development_frame,
    max_drawdown,
    profit_factor,
    simulate_symbol,
)

PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-allin-v7-preholdout-v1"
CONFIG = EXPERIMENT / "config.json"
OUT = EXPERIMENT / "results"
SOURCE_ATTACHMENT = Path(
    "/Users/zhangzc/.codex/attachments/"
    "a8101ae7-0925-4f31-baf5-79c6be603338/pasted-text.txt"
)
SYMBOL_RE = re.compile(r"^okx_(?P<symbol>.+)_15m_\d+\.csv$")
INITIAL_CAPITAL = 500.0
PERMUTATIONS = 10_000


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def arm_from_dict(raw: dict[str, Any]) -> Arm:
    return Arm(
        name=str(raw["name"]),
        signal_kind=str(raw["signal_kind"]),
        sizing_kind=str(raw["sizing_kind"]),
        base_leverage=float(raw.get("base_leverage", 4.0)),
        risk_per_trade_percent=float(raw.get("risk_per_trade_percent", 2.0)),
        max_leverage=float(raw.get("max_leverage", 13.0)),
        time_boosts=bool(raw.get("time_boosts", False)),
        skip_logic=bool(raw.get("skip_logic", True)),
    )


def signal_params(raw: dict[str, Any]) -> SignalParameters:
    return SignalParameters(**raw)


def symbol_from_path(path: Path) -> str:
    match = SYMBOL_RE.match(path.name)
    if match is None:
        raise ValueError(f"unexpected kline filename: {path.name}")
    return match.group("symbol")


def _bucket_by_symbol_month(frame: pd.DataFrame, mask: np.ndarray, buckets: int) -> np.ndarray:
    result = np.full(len(frame), -1, dtype=int)
    times = pd.to_datetime(frame["open_time"], utc=True)
    month = times.dt.strftime("%Y-%m")
    eligible = np.flatnonzero(mask & np.isfinite(frame["atr"].to_numpy(dtype=float)))
    if len(eligible) == 0:
        return result
    helper = pd.DataFrame(
        {"i": eligible, "month": month.iloc[eligible].to_numpy(), "atr": frame["atr"].iloc[eligible].to_numpy()}
    )
    for _, group in helper.groupby("month", sort=False):
        if len(group) < buckets:
            result[group["i"].to_numpy(dtype=int)] = 0
            continue
        # Rank first makes ties deterministic while preserving equal-count bins.
        ranks = group["atr"].rank(method="first")
        assigned = pd.qcut(ranks, q=buckets, labels=False).to_numpy(dtype=int)
        result[group["i"].to_numpy(dtype=int)] = assigned
    return result


def control_outcome(
    frame: pd.DataFrame,
    *,
    signal_i: int,
    direction: int,
    holding_bars: int,
    params: SignalParameters,
    round_trip_cost: float,
) -> dict[str, Any]:
    """Replay a matched random entry with the same stop, BE, horizon and cost."""

    entry_i = signal_i + 1
    exit_i = entry_i + max(0, int(holding_bars))
    if entry_i >= len(frame) or exit_i >= len(frame):
        raise IndexError("control horizon leaves the frame")
    open_ = frame["open"].to_numpy(dtype=float)
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    entry = float(open_[entry_i])
    distance = min(
        float(frame["atr"].iloc[signal_i]) * params.atr_mult,
        entry * params.max_sl_percent / 100.0,
    )
    stop = entry - direction * distance
    reason = "copied_horizon"
    fill = float(close[exit_i])
    actual_exit_i = exit_i
    for i in range(entry_i, exit_i + 1):
        if direction > 0 and low[i] <= stop:
            fill = min(float(open_[i]), stop)
            reason = "stop"
            actual_exit_i = i
            break
        if direction < 0 and high[i] >= stop:
            fill = max(float(open_[i]), stop)
            reason = "stop"
            actual_exit_i = i
            break
        if direction > 0 and high[i] >= entry * (
            1.0 + params.break_even_trigger_percent / 100.0
        ):
            stop = max(stop, entry * (1.0 + params.break_even_offset_percent / 100.0))
        elif direction < 0 and low[i] <= entry * (
            1.0 - params.break_even_trigger_percent / 100.0
        ):
            stop = min(stop, entry * (1.0 - params.break_even_offset_percent / 100.0))
    gross = direction * (fill / entry - 1.0)
    return {
        "control_entry_i": entry_i,
        "control_exit_i": actual_exit_i,
        "control_entry_price": entry,
        "control_exit_price": fill,
        "control_exit_reason": reason,
        "control_gross_return": gross,
        "control_net_return": gross - round_trip_cost,
    }


def build_controls(
    frame: pd.DataFrame,
    trades: pd.DataFrame,
    *,
    split_start: pd.Timestamp,
    split_end: pd.Timestamp,
    arm: Arm,
    params: SignalParameters,
    config: dict[str, Any],
    round_trip_cost: float,
) -> pd.DataFrame:
    """Exact-stratum deterministic controls; never widens an empty stratum."""

    if trades.empty:
        return pd.DataFrame()
    times = pd.to_datetime(frame["open_time"], utc=True)
    split_mask = ((times >= split_start) & (times < split_end)).to_numpy()
    if arm.signal_kind == "v7":
        any_signal = frame["v7_long"].to_numpy(dtype=bool) | frame["v7_short"].to_numpy(dtype=bool)
    else:
        any_signal = frame["cross_long"].to_numpy(dtype=bool) | frame["cross_short"].to_numpy(dtype=bool)
    base_pool = split_mask & frame["entry_allowed"].to_numpy(dtype=bool) & ~any_signal
    buckets = _bucket_by_symbol_month(
        frame,
        split_mask & frame["entry_allowed"].to_numpy(dtype=bool),
        int(config["atr_buckets"]),
    )
    hk = times.dt.tz_convert("Asia/Hong_Kong")
    months = times.dt.strftime("%Y-%m").to_numpy()
    blocks = (hk.dt.hour.to_numpy(dtype=int) // int(config["hk_time_block_hours"])).astype(int)
    n_per = int(config["n_per_trade"])
    seed = str(config["seed"])
    # Index the admissible pool once. Building a full-frame boolean mask for
    # every trade is quadratic on multi-year 15m histories.
    pool_by_stratum: dict[tuple[str, int, int], np.ndarray] = {}
    pool_indices = np.flatnonzero(base_pool & (buckets >= 0))
    if len(pool_indices):
        helper = pd.DataFrame(
            {
                "i": pool_indices,
                "month": months[pool_indices],
                "block": blocks[pool_indices],
                "bucket": buckets[pool_indices],
            }
        )
        for key, group in helper.groupby(["month", "block", "bucket"], sort=False):
            pool_by_stratum[(str(key[0]), int(key[1]), int(key[2]))] = group["i"].to_numpy(dtype=int)
    rows: list[dict[str, Any]] = []
    for trade in trades.itertuples(index=False):
        signal_i = int(trade.signal_i)
        horizon = int(trade.holding_bars)
        if buckets[signal_i] < 0:
            continue
        eligible = pool_by_stratum.get(
            (str(months[signal_i]), int(blocks[signal_i]), int(buckets[signal_i])),
            np.empty(0, dtype=int),
        )
        eligible = eligible[
            (eligible + 1 + horizon < len(frame))
            & (np.abs(eligible - signal_i) > horizon + 1)
        ]
        direction = 1 if str(trade.direction) == "long" else -1
        # Control identity excludes the arm name. Arms that differ only in
        # sizing therefore receive the exact same random entries, which keeps
        # the single-variable comparison paired instead of adding RNG noise.
        control_candidate_id = (
            f"{trade.split}|{trade.symbol}|{signal_i}|{int(trade.entry_i)}|"
            f"{direction}|{horizon}"
        )
        candidate_id = str(trade.trade_id)
        selected = deterministic_control_indices(
            control_candidate_id, eligible, n=n_per, seed=seed
        )
        for rank, control_i in enumerate(selected):
            outcome = control_outcome(
                frame,
                signal_i=control_i,
                direction=direction,
                holding_bars=horizon,
                params=params,
                round_trip_cost=round_trip_cost,
            )
            rows.append(
                {
                    "trade_id": candidate_id,
                    "arm": arm.name,
                    "symbol": str(trade.symbol),
                    "split": str(trade.split),
                    "control_rank": rank,
                    "direction": str(trade.direction),
                    "holding_bars": horizon,
                    "candidate_signal_i": signal_i,
                    "candidate_signal_time": trade.signal_time,
                    "control_signal_i": control_i,
                    "control_signal_time": times.iloc[control_i],
                    "match_month": months[signal_i],
                    "match_hk_6h_block": int(blocks[signal_i]),
                    "match_atr_quintile": int(buckets[signal_i]),
                    "selection_sha256": hashlib.sha256(
                        f"{seed}|{control_candidate_id}|{control_i}".encode("utf-8")
                    ).hexdigest(),
                    **outcome,
                }
            )
    return pd.DataFrame(rows)


def signflip_p(values: Iterable[float], *, seed: int, permutations: int) -> float:
    array = np.asarray(list(values), dtype=float)
    array = array[np.isfinite(array)]
    if len(array) < 2:
        return float("nan")
    observed = float(array.mean())
    rng = np.random.default_rng(seed)
    extreme = 0
    for _ in range(permutations):
        signs = rng.choice(np.array([-1.0, 1.0]), size=len(array))
        if float(np.mean(array * signs)) >= observed:
            extreme += 1
    return (extreme + 1) / (permutations + 1)


def aggregate_portfolio(daily: pd.DataFrame) -> pd.DataFrame:
    if daily.empty:
        return pd.DataFrame()
    portfolio = (
        daily.groupby(["arm", "split", "open_time"], as_index=False)["normalized_equity"]
        .mean()
        .rename(columns={"normalized_equity": "portfolio_equity"})
    )
    return portfolio.sort_values(["arm", "split", "open_time"]).reset_index(drop=True)


def summarize(
    trades: pd.DataFrame,
    controls: pd.DataFrame,
    portfolio: pd.DataFrame,
    arms: list[Arm],
    splits: dict[str, tuple[pd.Timestamp, pd.Timestamp]],
    round_trip_cost: float,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rankings: dict[str, Any] = {}
    for arm_index, arm in enumerate(arms):
        for split_index, split in enumerate(splits):
            current = trades[(trades["arm"] == arm.name) & (trades["split"] == split)].copy()
            current_controls = controls[
                (controls["arm"] == arm.name) & (controls["split"] == split)
            ].copy()
            current_portfolio = portfolio[
                (portfolio["arm"] == arm.name) & (portfolio["split"] == split)
            ].sort_values("open_time")
            if current.empty:
                continue
            means = (
                current_controls.groupby("trade_id", as_index=False)
                .agg(
                    control_gross_return=("control_gross_return", "mean"),
                    control_net_return=("control_net_return", "mean"),
                    n_controls=("control_rank", "count"),
                )
            )
            joined = current.merge(means, on="trade_id", how="left", validate="one_to_one")
            matched = joined[joined["n_controls"].fillna(0) > 0].copy()
            scores = current["score"].to_numpy(dtype=float)
            gross = current["gross_return"].to_numpy(dtype=float)
            top_n = max(1, int(math.ceil(len(current) * 0.10)))
            order = np.argsort(scores)[::-1]
            top_indices = order[:top_n]
            perm = permutation_test(
                scores,
                gross,
                statistic=lambda _scores, outcomes, idx=top_indices: float(np.mean(outcomes[idx])),
                n_permutations=PERMUTATIONS,
                alternative="greater",
                seed=20260820 + arm_index * 10 + split_index,
            )
            matched["paired_excess"] = matched["net_return"] - matched["control_net_return"]
            matched_top = matched.nlargest(max(1, int(math.ceil(len(matched) * 0.10))), "score")
            if current_portfolio.empty:
                portfolio_return = float("nan")
                portfolio_dd = float("nan")
            else:
                portfolio_return = float(current_portfolio["portfolio_equity"].iloc[-1] - 1.0)
                portfolio_dd = max_drawdown(current_portfolio["portfolio_equity"])
            key = f"{arm.name}:{split}"
            rankings[key] = {
                "n_trades": int(len(current)),
                "score_definition": "abs(osc)" if arm.signal_kind == "v7" else "abs(SMA10-SMA60)/close",
                "top_decile_n": top_n,
                "top_decile_gross_bp": float(np.mean(gross[top_indices]) * 1e4),
                "top_decile_net_bp": float((np.mean(gross[top_indices]) - round_trip_cost) * 1e4),
                "permutation_p": float(perm.p_value),
                "permutations": PERMUTATIONS,
                "auc_net_positive": auc_from_scores(scores, current["net_return"].to_numpy() > 0.0),
                "matched_top_decile_control_net_bp": (
                    float(matched_top["control_net_return"].mean() * 1e4)
                    if not matched_top.empty
                    else float("nan")
                ),
            }
            rows.append(
                {
                    "arm": arm.name,
                    "split": split,
                    "n_symbols": int(current["symbol"].nunique()),
                    "n_trades": int(len(current)),
                    "gross_bp_per_trade": float(current["gross_return"].mean() * 1e4),
                    "net_bp_per_trade": float(current["net_return"].mean() * 1e4),
                    "win_rate": float((current["net_return"] > 0.0).mean()),
                    "profit_factor": profit_factor(current["pnl"]),
                    "average_leverage": float(current["leverage"].mean()),
                    "portfolio_return": portfolio_return,
                    "portfolio_max_drawdown": portfolio_dd,
                    "matched_trade_coverage": float(len(matched) / len(current)),
                    "matched_control_net_bp": (
                        float(matched["control_net_return"].mean() * 1e4)
                        if not matched.empty
                        else float("nan")
                    ),
                    "paired_excess_bp": (
                        float(matched["paired_excess"].mean() * 1e4)
                        if not matched.empty
                        else float("nan")
                    ),
                    "paired_signflip_p": signflip_p(
                        matched["paired_excess"],
                        seed=20260920 + arm_index * 10 + split_index,
                        permutations=PERMUTATIONS,
                    ),
                    "top_decile_net_bp": rankings[key]["top_decile_net_bp"],
                    "permutation_p": rankings[key]["permutation_p"],
                    "auc_net_positive": rankings[key]["auc_net_positive"],
                }
            )
    return pd.DataFrame(rows), rankings


def build_charts(summary: pd.DataFrame, portfolio: pd.DataFrame, out_dir: Path) -> list[dict[str, Any]]:
    validation = summary[summary["split"] == "validation"].copy()
    arm_order = validation["arm"].tolist()
    palette = {
        "sma_cross_only": "#64748B",
        "v7_fixed_boost": "#2563EB",
        "v7_fixed_no_boost": "#D97706",
        "v7_risk_2pct": "#6B8E23",
        "v7_risk_2pct_no_skip": "#C2417A",
    }
    chart_map: list[dict[str, Any]] = []

    fig, axes = plt.subplots(2, 1, figsize=(11, 8), constrained_layout=True)
    for arm in arm_order:
        group = portfolio[(portfolio["split"] == "validation") & (portfolio["arm"] == arm)]
        axes[0].plot(
            pd.to_datetime(group["open_time"], utc=True),
            group["portfolio_equity"],
            label=arm,
            color=palette.get(arm, "#334155"),
            linewidth=1.8,
        )
    axes[0].axhline(1.0, color="#334155", linewidth=0.8, linestyle="--")
    axes[0].set_title("Validation equal-weight normalized equity")
    axes[0].set_ylabel("Equity / initial capital")
    axes[0].grid(axis="y", color="#CBD5E1", linewidth=0.6, alpha=0.7)
    axes[0].legend(ncol=2, fontsize=8, frameon=False)

    x = np.arange(len(validation))
    axes[1].bar(
        x,
        validation["portfolio_max_drawdown"] * 100.0,
        color=[palette.get(name, "#334155") for name in arm_order],
        edgecolor="#334155",
        linewidth=0.6,
    )
    axes[1].set_xticks(x, arm_order, rotation=18, ha="right")
    axes[1].set_ylabel("Maximum drawdown (%)")
    axes[1].set_title("Validation portfolio maximum drawdown")
    axes[1].set_ylim(bottom=0)
    axes[1].grid(axis="y", color="#CBD5E1", linewidth=0.6, alpha=0.7)
    path = out_dir / "validation_equity_drawdown.png"
    fig.savefig(path, dpi=160, facecolor="white")
    plt.close(fig)
    chart_map.append(
        {
            "section": "validation risk/return",
            "question": "How did the preregistered sizing/skip arms change equity and drawdown?",
            "family": "trend + comparison",
            "fields": ["open_time", "portfolio_equity", "portfolio_max_drawdown", "arm"],
            "takeaway": "Compare return paths and drawdown without changing signal/barrier thresholds.",
            "palette_policy": "relaxed multi-category, five fixed roots plus neutrals",
            "artifact": str(path.relative_to(PROJECT)),
        }
    )

    fig, ax = plt.subplots(figsize=(11, 5.8), constrained_layout=True)
    width = 0.36
    ax.bar(
        x - width / 2,
        validation["net_bp_per_trade"],
        width,
        label="Strategy net",
        color="#2563EB",
        edgecolor="#334155",
        linewidth=0.6,
    )
    ax.bar(
        x + width / 2,
        validation["matched_control_net_bp"],
        width,
        label="Matched random net",
        color="#D6B55A",
        edgecolor="#334155",
        linewidth=0.6,
    )
    ax.axhline(0.0, color="#334155", linewidth=0.9)
    ax.set_xticks(x, arm_order, rotation=18, ha="right")
    ax.set_ylabel("Mean net return (bp/trade)")
    ax.set_title("Validation strategy and matched-random entry returns")
    ax.grid(axis="y", color="#CBD5E1", linewidth=0.6, alpha=0.7)
    ax.legend(frameon=False)
    path = out_dir / "validation_matched_control.png"
    fig.savefig(path, dpi=160, facecolor="white")
    plt.close(fig)
    chart_map.append(
        {
            "section": "matched null",
            "question": "Does signal timing beat random entries in the same market conditions?",
            "family": "comparison",
            "fields": ["arm", "net_bp_per_trade", "matched_control_net_bp"],
            "takeaway": "Separate signal timing from symbol/time/volatility beta.",
            "palette_policy": "hard two-root cap plus neutral zero line",
            "artifact": str(path.relative_to(PROJECT)),
        }
    )
    return chart_map


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--max-symbols", type=int, default=0, help="diagnostic subset only")
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    if sha256_file(SOURCE_ATTACHMENT) != config["source_attachment_sha256"]:
        raise RuntimeError("user-supplied Pine attachment hash changed")
    params = signal_params(config["signal_parameters"])
    arms = [arm_from_dict(item) for item in config["arms"]]
    safe_end = pd.Timestamp(config["safe_end_exclusive"])
    holdout = pd.Timestamp(config["holdout_start"])
    development_start = pd.Timestamp(config["development_start"])
    validation_start = pd.Timestamp(config["validation_start"])
    splits = {
        "development": (development_start, validation_start),
        "validation": (validation_start, safe_end),
    }
    if not safe_end < holdout:
        raise RuntimeError("safe analysis end must precede holdout")
    data_paths = sorted(PROJECT.glob(config["data_glob"]))
    if args.max_symbols:
        data_paths = data_paths[: args.max_symbols]
    if not data_paths:
        raise RuntimeError("no kline files matched the frozen data glob")

    OUT.mkdir(parents=True, exist_ok=True)
    all_trades: list[pd.DataFrame] = []
    all_daily: list[pd.DataFrame] = []
    all_controls: list[pd.DataFrame] = []
    quality_rows: list[dict[str, Any]] = []
    for position, path in enumerate(data_paths, start=1):
        symbol = symbol_from_path(path)
        raw = load_development_frame(path, safe_end=safe_end, holdout_start=holdout)
        gaps = raw["open_time"].diff().dropna()
        if bool((gaps != EXPECTED_BAR).any()):
            raise RuntimeError(f"{symbol} has non-15m gaps; refuse cross-gap indicators")
        frame = add_indicators(raw, params)
        quality_rows.append(
            {
                "symbol": symbol,
                "rows": len(frame),
                "first_bar": frame["open_time"].min(),
                "last_bar": frame["open_time"].max(),
                "gap_count": int((gaps != EXPECTED_BAR).sum()),
                "v7_raw_signals": int(frame["v7_long"].sum() + frame["v7_short"].sum()),
                "cross_raw_signals": int(frame["cross_long"].sum() + frame["cross_short"].sum()),
            }
        )
        for split, (split_start, split_end) in splits.items():
            for arm in arms:
                trades, daily = simulate_symbol(
                    frame,
                    symbol=symbol,
                    arm=arm,
                    start=split_start,
                    end=split_end,
                    params=params,
                    round_trip_cost=float(config["round_trip_cost"]),
                    initial_capital=INITIAL_CAPITAL,
                )
                if not trades.empty:
                    trades["split"] = split
                    trades["trade_id"] = trades.apply(
                        lambda row: (
                            f"{arm.name}|{split}|{symbol}|{int(row['signal_i'])}|"
                            f"{int(row['entry_i'])}"
                        ),
                        axis=1,
                    )
                    all_trades.append(trades)
                    matched = build_controls(
                        frame,
                        trades,
                        split_start=split_start,
                        split_end=split_end,
                        arm=arm,
                        params=params,
                        config=config["matched_control"],
                        round_trip_cost=float(config["round_trip_cost"]),
                    )
                    if not matched.empty:
                        all_controls.append(matched)
                if not daily.empty:
                    daily["split"] = split
                    all_daily.append(daily)
        print(f"[{position:02d}/{len(data_paths):02d}] {symbol}: {len(frame):,} rows", flush=True)

    trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    daily = pd.concat(all_daily, ignore_index=True) if all_daily else pd.DataFrame()
    controls = pd.concat(all_controls, ignore_index=True) if all_controls else pd.DataFrame()
    quality = pd.DataFrame(quality_rows)
    portfolio = aggregate_portfolio(daily)
    summary, rankings = summarize(
        trades,
        controls,
        portfolio,
        arms,
        splits,
        float(config["round_trip_cost"]),
    )
    chart_map = build_charts(summary, portfolio, OUT)

    trades.to_csv(OUT / "trades.csv", index=False)
    controls.to_csv(OUT / "matched_controls.csv", index=False)
    daily.to_csv(OUT / "daily_symbol_equity.csv", index=False)
    portfolio.to_csv(OUT / "portfolio_daily.csv", index=False)
    quality.to_csv(OUT / "data_quality.csv", index=False)
    summary.to_csv(OUT / "summary.csv", index=False)
    (OUT / "chart_map.json").write_text(
        json.dumps(chart_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    payload = {
        "experiment_id": config["experiment_id"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "diagnostic_preholdout",
        "holdout_consumed": False,
        "training_eligible": False,
        "production_eligible": False,
        "data": {
            "glob": config["data_glob"],
            "symbols": int(quality["symbol"].nunique()),
            "rows": int(quality["rows"].sum()),
            "first_bar": str(quality["first_bar"].min()),
            "last_bar": str(quality["last_bar"].max()),
            "safe_end_exclusive": str(safe_end),
            "holdout_start": str(holdout),
            "gap_count": int(quality["gap_count"].sum()),
        },
        "protocol": {
            "signal_parameters": asdict(params),
            "arms": [asdict(arm) for arm in arms],
            "round_trip_cost": float(config["round_trip_cost"]),
            "splits": {key: [str(value[0]), str(value[1])] for key, value in splits.items()},
            "matched_control": config["matched_control"],
        },
        "ranking": rankings,
        "summary": summary.replace({np.nan: None, np.inf: None, -np.inf: None}).to_dict("records"),
        "source_identity": {
            "attachment_sha256": sha256_file(SOURCE_ATTACHMENT),
            "config_sha256": sha256_file(args.config),
            "runner_sha256": sha256_file(Path(__file__)),
            "engine_sha256": sha256_file(
                PROJECT / "yoyo/layers/l3_backtest/pine_allin_v7.py"
            ),
        },
        "caveats": [
            "Python is an auditable translation, not the TradingView broker emulator.",
            "The cache is a current 54-symbol survival universe, not a historical listing snapshot.",
            "Portfolio equity is equal-weight across symbols available on each date.",
            "Copied-horizon matched controls isolate entry timing but do not reproduce signal-driven reversal exits.",
            "Validation is pre-holdout and can guide research; it is not final acceptance or forward evidence.",
        ],
    }
    (OUT / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(summary.to_string(index=False), flush=True)
    print(f"wrote {OUT.relative_to(PROJECT)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
