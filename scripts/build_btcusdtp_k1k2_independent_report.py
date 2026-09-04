#!/usr/bin/env python3
"""Build the audited BTCUSDT.P 15m/5m independent-strategy report.

Inputs are the frozen pre-holdout experiment artifacts produced by
``scripts.optimize_btcusdtp_k1k2_independent_timeframes``.  The script does
not read raw OHLCV, refit parameters, change execution assumptions, or touch
the repository holdout.  It derives diagnostic tables and publication figures
from already frozen development and exploratory-audit trade ledgers.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / (
    "experiments/active/"
    "exp-btcusdtp-k1k2-15m-5m-independent-preholdout-20260904-v1"
)
RESULTS = EXPERIMENT / "results"
PREDECESSOR = PROJECT / (
    "experiments/active/"
    "exp-btcusdtp-k1k2-15m-5m-params-preholdout-20260904-v2/results/"
    "validation_metrics_corrected.csv"
)
REPORT = PROJECT / (
    "analysis/p1_btcusdtp_k1k2_15m_5m_independent_research_20260904.md"
)

TEAL = "#0F8B8D"
ORANGE = "#F28E2B"
RED = "#D1495B"
GOLD = "#E3B23C"
NAVY = "#23395D"
GREY = "#7C8798"
LIGHT_GREY = "#D7DCE2"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fmt(value: float, digits: int = 2, signed: bool = False) -> str:
    if not np.isfinite(value):
        return "n/a"
    prefix = "+" if signed else ""
    return f"{value:{prefix}.{digits}f}"


def markdown_table(frame: pd.DataFrame) -> str:
    columns = [str(column) for column in frame.columns]
    header = "| " + " | ".join(columns) + " |"
    separator = "|" + "|".join("---" for _ in columns) + "|"
    rows = []
    for values in frame.itertuples(index=False, name=None):
        rows.append("| " + " | ".join(str(value) for value in values) + " |")
    return "\n".join([header, separator, *rows])


def annotate_bars(axis: plt.Axes, bars: Any, *, digits: int = 1) -> None:
    for bar in bars:
        height = float(bar.get_height())
        offset = 3 if height >= 0 else -14
        vertical = "bottom" if height >= 0 else "top"
        axis.annotate(
            f"{height:.{digits}f}",
            (bar.get_x() + bar.get_width() / 2.0, height),
            xytext=(0, offset),
            textcoords="offset points",
            ha="center",
            va=vertical,
            fontsize=9,
            color=NAVY,
        )


def build_strategy_comparison(
    audit: pd.DataFrame, predecessor: pd.DataFrame, months: float
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for bar in ("15m", "5m"):
        current = audit.loc[audit["bar"].eq(bar)].iloc[0]
        old = predecessor.loc[predecessor["bar"].str.strip().eq(bar)].iloc[0]
        rows.append(
            {
                "bar": bar,
                "old_events": int(old["events"]),
                "new_events": int(current["events"]),
                "density_multiplier": float(current["events"] / old["events"]),
                "old_events_per_month": float(old["events"] / months),
                "new_events_per_month": float(current["events"] / months),
                "old_mean_net_bp": float(old["mean_net_bp"]),
                "new_mean_gross_bp": float(current["mean_gross_bp"]),
                "new_mean_net_bp": float(current["mean_net_bp"]),
                "new_win_rate": float(current["win_rate"]),
                "new_profit_factor": float(current["profit_factor"]),
                "matched_events": int(current["matched_events"]),
                "matched_excess_bp": float(current["matched_control_excess_bp"]),
                "matched_p": float(current["paired_signflip_p_one_sided"]),
            }
        )
    return pd.DataFrame(rows)


def build_failure_mechanics() -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    continuation: list[dict[str, Any]] = []
    for bar in ("15m", "5m"):
        trades = pd.read_csv(RESULTS / f"audit_{bar}_selected_trades.csv.gz")
        outcome = trades["outcome"].astype(str)
        is_stop = outcome.str.startswith("sl")
        groups = [
            ("SL before 0.5R", is_stop & trades["mfe_r"].lt(0.5)),
            (
                "SL after 0.5–1.5R",
                is_stop & trades["mfe_r"].ge(0.5) & trades["mfe_r"].lt(1.5),
            ),
            ("SL after ≥1.5R", is_stop & trades["mfe_r"].ge(1.5)),
            ("Protected stop", outcome.str.startswith("protected_stop")),
            ("TP 3R", outcome.eq("tp")),
            ("Timeout", outcome.eq("timeout")),
        ]
        for label, mask in groups:
            subset = trades.loc[mask]
            rows.append(
                {
                    "bar": bar,
                    "failure_stage": label,
                    "events": int(len(subset)),
                    "share": float(len(subset) / len(trades)),
                    "mean_net_bp": float(subset["net_return"].mean() * 1e4)
                    if len(subset)
                    else np.nan,
                }
            )

        stops = trades.loc[is_stop]
        winners = trades.loc[outcome.eq("tp")]
        continuation.append(
            {
                "bar": bar,
                "stops": int(len(stops)),
                "stops_later_3r": int(stops["horizon_mfe_r"].ge(3.0).sum()),
                "stops_later_3r_share": float(stops["horizon_mfe_r"].ge(3.0).mean()),
                "stops_later_4r": int(stops["horizon_hit_4r"].sum()),
                "stops_later_5r": int(stops["horizon_hit_5r"].sum()),
                "stops_later_6r": int(stops["horizon_hit_6r"].sum()),
                "tp_events": int(len(winners)),
                "tp_later_4r": int(winners["horizon_hit_4r"].sum()),
                "tp_later_5r": int(winners["horizon_hit_5r"].sum()),
                "tp_later_6r": int(winners["horizon_hit_6r"].sum()),
            }
        )
    return pd.DataFrame(rows), pd.DataFrame(continuation)


def build_risk_bins() -> pd.DataFrame:
    edges = [0.15, 0.30, 0.50, 0.75, 1.00, 1.50, 2.500001]
    labels = ["0.15–0.30", "0.30–0.50", "0.50–0.75", "0.75–1.00", "1.00–1.50", "1.50–2.50"]
    rows: list[dict[str, Any]] = []
    for bar in ("15m", "5m"):
        trades = pd.read_csv(RESULTS / f"audit_{bar}_selected_trades.csv.gz")
        trades["risk_bin"] = pd.cut(
            trades["stop_distance_atr"],
            bins=edges,
            labels=labels,
            right=False,
            include_lowest=True,
        )
        for label in labels:
            subset = trades.loc[trades["risk_bin"].astype(str).eq(label)]
            rows.append(
                {
                    "bar": bar,
                    "stop_distance_atr": label,
                    "events": int(len(subset)),
                    "mean_net_bp": float(subset["net_return"].mean() * 1e4)
                    if len(subset)
                    else np.nan,
                    "win_rate": float(subset["net_return"].gt(0).mean())
                    if len(subset)
                    else np.nan,
                }
            )
    return pd.DataFrame(rows)


def build_feature_stability() -> pd.DataFrame:
    features = [
        "secondary_score",
        "stop_distance_atr",
        "gap_bars",
        "k1_body_ratio",
        "k1_range_atr",
        "k1_close_location",
        "k1_cross_depth_atr",
        "k2_wick_share",
        "k2_body_ratio",
        "k2_rejection_close_location",
        "k2_touch_depth_atr",
        "path_close_share",
        "path_colour_share",
    ]
    rows: list[dict[str, Any]] = []
    for bar in ("15m", "5m"):
        dev = pd.read_csv(RESULTS / f"development_{bar}_selected_trades.csv.gz")
        audit = pd.read_csv(RESULTS / f"audit_{bar}_selected_trades.csv.gz")
        for feature in features:
            dev_corr = dev[[feature, "net_return"]].corr(method="spearman").iloc[0, 1]
            audit_corr = audit[[feature, "net_return"]].corr(method="spearman").iloc[0, 1]
            threshold = float(dev[feature].quantile(0.75))
            dev_top = dev.loc[dev[feature].ge(threshold), "net_return"].mean() * 1e4
            audit_top = audit.loc[audit[feature].ge(threshold), "net_return"].mean() * 1e4
            rows.append(
                {
                    "bar": bar,
                    "feature": feature,
                    "development_spearman": float(dev_corr),
                    "audit_spearman": float(audit_corr),
                    "development_q75": threshold,
                    "development_top_quartile_net_bp": float(dev_top),
                    "audit_above_dev_q75_events": int(audit[feature].ge(threshold).sum()),
                    "audit_above_dev_q75_net_bp": float(audit_top),
                    "same_correlation_sign": bool(np.sign(dev_corr) == np.sign(audit_corr)),
                }
            )
    return pd.DataFrame(rows)


def plot_density_and_expectancy(comparison: pd.DataFrame) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), constrained_layout=True)
    x = np.arange(len(comparison))
    width = 0.34
    old_density = axes[0].bar(
        x - width / 2,
        comparison["old_events_per_month"],
        width,
        label="Previous hard filters",
        color=LIGHT_GREY,
        edgecolor=GREY,
    )
    new_density = axes[0].bar(
        x + width / 2,
        comparison["new_events_per_month"],
        width,
        label="Independent design",
        color=TEAL,
    )
    axes[0].set_title("Signal density increased")
    axes[0].set_ylabel("Accepted events / calendar month")
    axes[0].set_xticks(x, comparison["bar"])
    axes[0].legend(frameon=False)
    annotate_bars(axes[0], old_density)
    annotate_bars(axes[0], new_density)

    old_net = axes[1].bar(
        x - width / 2,
        comparison["old_mean_net_bp"],
        width,
        label="Previous hard filters",
        color=LIGHT_GREY,
        edgecolor=GREY,
    )
    new_net = axes[1].bar(
        x + width / 2,
        comparison["new_mean_net_bp"],
        width,
        label="Independent design",
        color=ORANGE,
    )
    axes[1].axhline(0, color=NAVY, linewidth=1)
    axes[1].set_title("But net expectancy stayed negative")
    axes[1].set_ylabel("Mean net return (bp / trade)")
    axes[1].set_xticks(x, comparison["bar"])
    annotate_bars(axes[1], old_net)
    annotate_bars(axes[1], new_net)
    for axis in axes:
        axis.spines[["top", "right"]].set_visible(False)
        axis.grid(axis="y", color="#E8EBEF", linewidth=0.8)
        axis.set_axisbelow(True)
    fig.suptitle("BTCUSDT.P frozen exploratory audit · 2025-01 to 2026-02", fontsize=14)
    fig.savefig(RESULTS / "independent_density_expectancy.png", dpi=170, bbox_inches="tight")
    plt.close(fig)


def plot_ranking(audit: pd.DataFrame) -> None:
    fig, axis = plt.subplots(figsize=(8.5, 4.8), constrained_layout=True)
    x = np.arange(len(audit))
    width = 0.25
    overall = axis.bar(
        x - width,
        audit["mean_net_bp"],
        width,
        label="All accepted",
        color=GREY,
    )
    score = axis.bar(
        x,
        audit["top_decile_mean_net_bp"],
        width,
        label="Equal-score top 10%",
        color=ORANGE,
    )
    baseline = axis.bar(
        x + width,
        audit["single_feature_top_decile_mean_net_bp"],
        width,
        label="K1 range top 10%",
        color=TEAL,
    )
    axis.axhline(0, color=NAVY, linewidth=1)
    axis.set_title("The 12-component score did not rank winners")
    axis.set_ylabel("Mean net return (bp / trade)")
    axis.set_xticks(x, audit["bar"])
    axis.legend(frameon=False, ncols=3, loc="lower center", bbox_to_anchor=(0.5, 1.01))
    axis.spines[["top", "right"]].set_visible(False)
    axis.grid(axis="y", color="#E8EBEF", linewidth=0.8)
    axis.set_axisbelow(True)
    for bars in (overall, score, baseline):
        annotate_bars(axis, bars)
    fig.savefig(RESULTS / "independent_score_ranking.png", dpi=170, bbox_inches="tight")
    plt.close(fig)


def plot_failure_mechanics(failures: pd.DataFrame, continuation: pd.DataFrame) -> None:
    order = [
        "SL before 0.5R",
        "SL after 0.5–1.5R",
        "SL after ≥1.5R",
        "Protected stop",
        "TP 3R",
        "Timeout",
    ]
    colors = ["#A63D40", RED, "#E76F51", GOLD, TEAL, LIGHT_GREY]
    fig, axis = plt.subplots(figsize=(9.5, 4.8), constrained_layout=True)
    x = np.arange(2)
    bottoms = np.zeros(2)
    for label, color in zip(order, colors):
        values = np.array(
            [
                failures.loc[
                    failures["bar"].eq(bar) & failures["failure_stage"].eq(label),
                    "share",
                ].iloc[0]
                for bar in ("15m", "5m")
            ]
        )
        bars = axis.bar(x, values, bottom=bottoms, label=label, color=color)
        for index, bar_patch in enumerate(bars):
            if values[index] >= 0.055:
                axis.text(
                    bar_patch.get_x() + bar_patch.get_width() / 2,
                    bottoms[index] + values[index] / 2,
                    f"{values[index] * 100:.1f}%",
                    ha="center",
                    va="center",
                    color="white" if label != "Protected stop" else NAVY,
                    fontsize=9,
                    fontweight="bold",
                )
        bottoms += values
    axis.set_xticks(x, ["15m", "5m"])
    axis.set_ylim(0, 1.18)
    axis.set_ylabel("Share of accepted trades")
    axis.set_title("Most trades were stopped before the move developed")
    for index, bar in enumerate(("15m", "5m")):
        row = continuation.loc[continuation["bar"].eq(bar)].iloc[0]
        axis.text(
            index,
            1.04,
            f"{row['stops_later_3r_share'] * 100:.1f}% of stops\nlater touched 3R",
            ha="center",
            va="bottom",
            fontsize=10,
            color=NAVY,
        )
    axis.legend(frameon=False, ncols=3, loc="lower center", bbox_to_anchor=(0.5, -0.30))
    axis.spines[["top", "right"]].set_visible(False)
    axis.grid(axis="y", color="#E8EBEF", linewidth=0.8)
    axis.set_axisbelow(True)
    fig.savefig(RESULTS / "independent_failure_mechanics.png", dpi=170, bbox_inches="tight")
    plt.close(fig)


def plot_risk_bins(risk_bins: pd.DataFrame) -> None:
    labels = risk_bins.loc[risk_bins["bar"].eq("15m"), "stop_distance_atr"].tolist()
    fig, axis = plt.subplots(figsize=(10, 4.8), constrained_layout=True)
    x = np.arange(len(labels))
    width = 0.36
    for offset, bar, color in [(-width / 2, "15m", TEAL), (width / 2, "5m", ORANGE)]:
        subset = risk_bins.loc[risk_bins["bar"].eq(bar)]
        bars = axis.bar(x + offset, subset["mean_net_bp"], width, label=bar, color=color)
        for patch, count in zip(bars, subset["events"]):
            axis.annotate(
                f"n={int(count)}",
                (patch.get_x() + patch.get_width() / 2, patch.get_height()),
                xytext=(0, -12),
                textcoords="offset points",
                ha="center",
                va="top",
                fontsize=8,
                color="white" if patch.get_height() < -8 else NAVY,
            )
    axis.axhline(0, color=NAVY, linewidth=1)
    axis.set_xticks(x, labels)
    axis.set_xlabel("K2-extreme stop distance / ATR14")
    axis.set_ylabel("Mean net return (bp / trade)")
    axis.set_title("Very tight K2 stops were worst; no bin was profitable")
    axis.legend(frameon=False)
    axis.spines[["top", "right"]].set_visible(False)
    axis.grid(axis="y", color="#E8EBEF", linewidth=0.8)
    axis.set_axisbelow(True)
    fig.savefig(RESULTS / "independent_risk_bins.png", dpi=170, bbox_inches="tight")
    plt.close(fig)


def make_report(
    config: dict[str, Any],
    selection: dict[str, Any],
    audit: pd.DataFrame,
    comparison: pd.DataFrame,
    failures: pd.DataFrame,
    continuation: pd.DataFrame,
    risk_bins: pd.DataFrame,
    features: pd.DataFrame,
) -> str:
    params_rows = []
    development_rows = []
    audit_rows = []
    ranking_rows = []
    control_rows = []
    slice_rows = []
    funnel_rows = []
    for bar in ("15m", "5m"):
        tf = selection["timeframes"][bar]
        params = tf["selected_params"]
        metrics = tf["selected_metrics"]
        current = audit.loc[audit["bar"].eq(bar)].iloc[0]
        params_rows.append(
            {
                "周期": bar,
                "SMA(HL2)": params["ma_period"],
                "K1→K2 距离": f"{params['gap_min_bars']}–{params['gap_max_bars']} bars",
                "等权分门": fmt(params["score_floor"], 2),
                "实际时间距离": "30–120 min",
                "均线记忆": "30 h" if bar == "15m" else "3 h 20 min",
            }
        )
        development_rows.append(
            {
                "周期": bar,
                "交易数": metrics["events"],
                "毛收益 bp/笔": fmt(metrics["mean_gross_bp"], 2, True),
                "净收益 bp/笔": fmt(metrics["mean_net_bp"], 2, True),
                "稳健分 bp": fmt(metrics["robust_score_bp"], 2, True),
                "最差半年 bp/笔": fmt(metrics["worst_fold_net_bp"], 2, True),
                "结论": "FAIL",
            }
        )
        audit_rows.append(
            {
                "周期": bar,
                "交易数": int(current["events"]),
                "毛收益 bp/笔": fmt(current["mean_gross_bp"], 2, True),
                "净收益 bp/笔": fmt(current["mean_net_bp"], 2, True),
                "胜率": f"{current['win_rate'] * 100:.1f}%",
                "PF": fmt(current["profit_factor"], 3),
                "TP / SL / 保护": f"{int(current['tp'])} / {int(current['sl'])} / {int(current['protected_stop'])}",
                "结论": "FAIL",
            }
        )
        ranking_rows.append(
            {
                "周期": bar,
                "排序池 n": int(current["ranking_events"]),
                "score AUC": fmt(current["score_auc_net_positive"], 3),
                "top10% n": int(current["top_decile_events"]),
                "top10% 毛 bp": fmt(current["top_decile_mean_gross_bp"], 2, True),
                "top10% 净 bp": fmt(current["top_decile_mean_net_bp"], 2, True),
                "top10% 胜率": f"{current['top_decile_win_rate'] * 100:.1f}%",
                "置换 p": fmt(current["ranking_permutation_p_one_sided"], 3),
                "单特征 top10% 净 bp": fmt(current["single_feature_top_decile_mean_net_bp"], 2, True),
            }
        )
        control_rows.append(
            {
                "周期": bar,
                "策略 n": int(current["events"]),
                "严格匹配 n": int(current["matched_events"]),
                "策略−随机 bp/笔": fmt(current["matched_control_excess_bp"], 3, True),
                "单侧符号置换 p": fmt(current["paired_signflip_p_one_sided"], 3),
                "判定": "不显著",
            }
        )
        slice_file = pd.read_csv(RESULTS / f"audit_{bar}_selected_slices.csv")
        for row in slice_file.itertuples(index=False):
            slice_rows.append(
                {
                    "周期": bar,
                    "审计切片": row.fold,
                    "n": int(row.events),
                    "毛 bp/笔": fmt(row.mean_gross_bp, 2, True),
                    "净 bp/笔": fmt(row.mean_net_bp, 2, True),
                    "胜率": f"{row.win_rate * 100:.1f}%",
                    "PF": fmt(row.profit_factor, 3),
                }
            )
        funnel = tf["funnel"]
        decisions = funnel["execution_decision_counts"]
        funnel_rows.append(
            {
                "周期": bar,
                "核心 K1→K2 对": funnel["core_pair_rows"],
                "距离+score 后": funnel["score_and_gap_candidate_rows"],
                "accepted": decisions.get("accepted", 0),
                "fee/risk 淘汰": decisions.get("fee_to_risk_above_max", 0),
                "风险过小": decisions.get("risk_atr_below_min", 0),
                "风险过大": decisions.get("risk_atr_above_max", 0),
                "冷却淘汰": decisions.get("cooldown", 0),
            }
        )

    comparison_display = pd.DataFrame(
        [
            {
                "周期": row.bar,
                "旧信号 n": row.old_events,
                "新信号 n": row.new_events,
                "密度倍数": f"{row.density_multiplier:.2f}×",
                "旧/月": fmt(row.old_events_per_month, 2),
                "新/月": fmt(row.new_events_per_month, 2),
                "旧净 bp/笔": fmt(row.old_mean_net_bp, 2, True),
                "新净 bp/笔": fmt(row.new_mean_net_bp, 2, True),
            }
            for row in comparison.itertuples(index=False)
        ]
    )
    failure_display = failures.copy()
    failure_display["share"] = failure_display["share"].map(lambda x: f"{x * 100:.1f}%")
    failure_display["mean_net_bp"] = failure_display["mean_net_bp"].map(
        lambda x: fmt(x, 2, True)
    )
    failure_display.columns = ["周期", "阶段", "n", "占比", "平均净 bp"]
    continuation_display = pd.DataFrame(
        [
            {
                "周期": row.bar,
                "止损 n": row.stops,
                "止损后 12h 内触 3R": f"{row.stops_later_3r} ({row.stops_later_3r_share * 100:.1f}%)",
                "止损后触 4R / 5R / 6R": f"{row.stops_later_4r} / {row.stops_later_5r} / {row.stops_later_6r}",
                "3R 止盈 n": row.tp_events,
                "止盈路径后触 4R / 5R / 6R": f"{row.tp_later_4r} / {row.tp_later_5r} / {row.tp_later_6r}",
            }
            for row in continuation.itertuples(index=False)
        ]
    )
    risk_display = risk_bins.copy()
    risk_display["mean_net_bp"] = risk_display["mean_net_bp"].map(lambda x: fmt(x, 2, True))
    risk_display["win_rate"] = risk_display["win_rate"].map(lambda x: f"{x * 100:.1f}%")
    risk_display.columns = ["周期", "止损距离 ATR", "n", "净 bp/笔", "胜率"]

    selected_features = features.loc[
        features["feature"].isin(
            [
                "secondary_score",
                "stop_distance_atr",
                "gap_bars",
                "k1_range_atr",
                "k2_wick_share",
                "k2_body_ratio",
                "k2_rejection_close_location",
                "k2_touch_depth_atr",
                "path_close_share",
                "path_colour_share",
            ]
        )
    ].copy()
    feature_display = pd.DataFrame(
        [
            {
                "周期": row.bar,
                "变量": row.feature,
                "开发 Spearman": fmt(row.development_spearman, 3, True),
                "审计 Spearman": fmt(row.audit_spearman, 3, True),
                "同号": "是" if row.same_correlation_sign else "否",
                "审计中高于开发 Q75 n": row.audit_above_dev_q75_events,
                "该组净 bp/笔": fmt(row.audit_above_dev_q75_net_bp, 2, True),
            }
            for row in selected_features.itertuples(index=False)
        ]
    )
    dev_folds = []
    for bar in ("15m", "5m"):
        metrics = selection["timeframes"][bar]["selected_metrics"]
        for fold in ("2023H1", "2023H2", "2024H1", "2024H2"):
            dev_folds.append(
                {
                    "周期": bar,
                    "开发折": fold,
                    "n": metrics[f"{fold}_events"],
                    "净 bp/笔": fmt(metrics[f"{fold}_mean_net_bp"], 2, True),
                }
            )

    report = f"""# BTCUSDT.P 15min / 5min 独立 K1→K2 研究（2026-09-04）

性质：P1、pre-holdout、两周期独立预注册开发 + 冻结探索审计。  
最终裁决：**两套都 REJECTED；信号密度问题解决了，但交易优势没有解决。**

## Executive Summary

这轮不再把 1h 参数机械缩放到小周期，而是给 15min 和 5min 各自选择均线周期、K1→K2
距离窗与形态总分。结果很清楚：15min 审计信号从 53 增至 181（{comparison.iloc[0]['density_multiplier']:.2f}×），
5min 从 74 增至 418（{comparison.iloc[1]['density_multiplier']:.2f}×）；但扣固定 20bp 往返成本后，
分别为 **{comparison.iloc[0]['new_mean_net_bp']:+.2f}bp/笔** 与
**{comparison.iloc[1]['new_mean_net_bp']:+.2f}bp/笔**。严格匹配随机入场后的超额也接近 0，p 值均远未过 0.01。

因此，先前“5min/15min 信号太少”的直接原因确实是把颜色、路径、实体等二级特征全部做成硬
否决；把它们改为评分后密度恢复。可交易性仍失败的主要矛盾则不是信号数量，而是 **K2 极值
止损与小周期噪声不匹配，加上形态评分没有排序力**。15min 有 46.2%、5min 有 54.9% 的止损单，
在被止损后同一冻结 12 小时路径里仍曾达到原定 3R；与此同时等权 score 的 AUC 都低于 0.5，
top-decile 反而更差。

![Density and expectancy](../experiments/active/exp-btcusdtp-k1k2-15m-5m-independent-preholdout-20260904-v1/results/independent_density_expectancy.png)

## 这轮实际得到的两套参数

{markdown_table(pd.DataFrame(params_rows))}

值得保留的跨周期规律是 **K1→K2 的实际时间距离都收敛到 30–120 分钟**；值得拒绝的假设是
“均线周期也应按实际时间等比例”。15min 选择 SMA120（30 小时），5min 保留 SMA40（3 小时
20 分）；5min 的 120/240/480 均线没有按预注册移动规则胜出。score floor 两边均保持最低 0.40，
说明加严总分没有产生足够收益改善。

## 交易规则（本轮冻结口径）

1. **K1：**方向实体必须从 SMA(HL2) 错侧真正贯穿到方向侧，穿越容差为 0；多头由下向上，
   空头镜像。
2. **K2：**影线必须真实触碰同一 SMA(HL2)，完整实体与收盘都留在方向侧；K2 不能用实体穿线。
3. **K1→K2：**15min 允许 2–8 根、5min 允许 6–24 根。中间 K 线站位、MA 颜色、K1/K2
   几何不再各自一票否决，而是组成 12 项等权因果 score，最低 0.40。
4. **入场：**K2 完成后的下一根开盘；多头止损为 K2 最低点，空头止损为 K2 最高点。
5. **经济门：**止损距离须在 0.15–2.50 ATR14，固定成本 0.2% 除以价格风险不得高于 1.25。
6. **出场：**3R 止盈；12 小时到期；同根同时触发按止损优先。收盘达到 1.5R 后启用覆盖手续费
   的保护止损。15min 冷却 24 根，5min 冷却 72 根，都是 6 小时。

注意：这不是当前可交易系统，而是本轮被证伪的研究合同；未写入 Pine、ACTIVE、frozen 或实盘。

## 预注册开发结果（2023–2024）

{markdown_table(pd.DataFrame(development_rows))}

{markdown_table(pd.DataFrame(dev_folds))}

15min 只接受了一次单变量移动：SMA40→SMA120；5min 只接受了距离窗 2–8→6–24。两个周期的
开发均值、稳健分和最差半年都仍为负，所以开发阶段已经没有通过。之后仍按预注册合同冻结参数，
仅做一次探索审计，没有在审计结果上回头调参。

开发漏斗进一步说明“少信号”发生在哪里：

{markdown_table(pd.DataFrame(funnel_rows))}

5min 最大的机械淘汰来自 fee-to-risk 门（7,179 个），本质是 K2 极值止损太近，以至固定 20bp
成本占风险过大；这不是简单把门放宽就能修复，因为放宽只会接受手续费相对风险更贵的交易。

## 冻结探索审计（2025-01-01 至 2026-02-28 16:00 UTC）

{markdown_table(pd.DataFrame(audit_rows))}

分时段结果没有隐藏的稳定盈利区：

{markdown_table(pd.DataFrame(slice_rows))}

15min 三段全部净亏；5min 三段也全部净亏。5min 的毛收益仅 +0.74bp/笔，远小于冻结的 20bp
成本；15min 连毛收益也是负值。失败不是某一个月偶发拖累。

### 信号数量：改善成立，但不是 edge

{markdown_table(comparison_display)}

“信号太少”这个问题已被定位并修复：旧逻辑把二级视觉特征当硬门，导致漏检；新逻辑保留核心
K1/K2 形态并软评分，信号密度达标。但新信号只是更多，不是更好，不能因数量提升而上线。

### 匹配随机对照：形态本身没有显著贡献

{markdown_table(pd.DataFrame(control_rows))}

随机对照按同月 × UTC 六小时块 × 当月 ATR14 五分位严格匹配，并复制方向、ATR 风险距离、出场、
时限与成本；找不到完全匹配时不放宽。5min 因此只有 388/418 笔进入配对检验。15min 超额
+0.138bp、5min -0.194bp，均约等于 0；单侧符号置换 p=0.486/0.529。也就是说，当前 K1→K2
核心形态并没有优于同环境随机时点。

### 12 项评分：不是“阈值不够好”，而是没有稳定排序力

{markdown_table(pd.DataFrame(ranking_rows))}

![Score ranking](../experiments/active/exp-btcusdtp-k1k2-15m-5m-independent-preholdout-20260904-v1/results/independent_score_ranking.png)

score AUC 为 0.488/0.487，等权 top-decile 的净收益降至 -30.29/-27.02bp；单独用 K1 range 排序
虽比等权 score 好，但仍为 -5.50/-13.95bp。结论不是“把 score_floor 从 0.40 调到 0.55”——
开发阶段已经逐档检查 0.40–0.70，未产生满足移动门槛的改进。需要重估二级特征的方向与交互，
而不是继续旋阈值。

以下是开发与审计相关方向的稳定性诊断；它是事后解释，不能直接变成新门：

{markdown_table(feature_display)}

若干蜡烛与路径特征换期即翻号；`stop_distance_atr` 的逐笔 Spearman 与粗分桶方向甚至不完全一致，
说明关系明显非线性且受 barrier 收益尺度影响。这解释了为什么“颜色越一致、形态越漂亮就越赚钱”
的等权先验没有成立，也不允许从这张事后表直接抄一个阈值。

## 失败交易的真正规律

![Failure mechanics](../experiments/active/exp-btcusdtp-k1k2-15m-5m-independent-preholdout-20260904-v1/results/independent_failure_mechanics.png)

{markdown_table(failure_display)}

{markdown_table(continuation_display)}

最重要的反直觉结果是：大量交易不是方向完全错，而是先扫掉紧贴 K2 极值的止损，再沿原方向
走出目标。这里的“后来触 3R”只是在原始 12 小时 OHLC 路径上做的诊断，不是假装止损后仍持仓
的收益回测，也不证明加宽止损必然有效；但它明确指出下一项应验证 **止损/入场几何**，而不是
继续堆 K 线颜色门。

按原始 K2 止损距离分桶也一致：

![Risk bins](../experiments/active/exp-btcusdtp-k1k2-15m-5m-independent-preholdout-20260904-v1/results/independent_risk_bins.png)

{markdown_table(risk_display)}

最紧的 <0.50 ATR 桶明显差，较宽风险桶相对改善，但所有桶仍为负。这个分桶是在冻结审计后做的
探索性归因，不能把 0.50 ATR 偷写成已验证阈值。

## 关于“盈利单止盈可以更高”

这个观察有数据支持：15min 的 26 笔 3R 止盈中，20/19/15 笔后来触及 4R/5R/6R；5min 的
77 笔中有 68/60/52 笔。也就是说，**赢家确实常有余程**。但当前更大的漏损是止损：132 笔
15min 止损和 277 笔 5min 止损中，分别 61 与 152 笔后来仍触 3R。直接把 TP 从 3R 提到 5R，
不会挽回这些已被扫出的交易，还可能把原本 3R 获利变成回撤。

正确顺序是：先单变量修复止损/入场；只有该变体在开发与新鲜验证上站住，再单独比较固定 3R、
部分 3R + runner 5R、或趋势尾仓。两者不能打包，否则无法知道收益来自哪项变化。

## 下一步建议与 Owner 决策门

建议下一实验只改一项：**K2 极值外加 ATR buffer**，候选保持为 0 / 0.10 / 0.20 / 0.30 /
0.50 ATR；K1/K2 形态、已选 MA/距离、下一根开盘、3R、12h、1.5R 保护与 0.2% 成本全部冻结。
这会改变 SL 障碍参数，按项目铁律必须由 Owner 再明确批准，不能在本轮偷偷回测。

如果 buffer 仍失败，第二个独立假设才是“等待扫 K2 极值后收回再入场”；它改变入场时机，不能与
buffer 同轮。只有止损/入场单变量通过后，才轮到提高 TP 或保留 runner。

本轮不建议：放宽 fee-to-risk 门、继续提高 score_floor、把两个周期合成一套参数、更新 TradingView
Pine、读取 2026-05-04 之后 holdout、promote 或部署。

## 数据、可复现性与审计边界

| 项目 | 值 |
|---|---|
| 合约 | OKX `BTC-USDT-SWAP`（TradingView `OKX:BTCUSDT.P`） |
| 原生源 | `{config['source']['path']}` |
| SHA256 | `{config['source']['sha256']}` |
| 原生行数 | {config['source']['rows']:,} 根 5min |
| 源时间 | {config['source']['first_time']} ～ {config['source']['last_time']} |
| 15min 构造 | UTC 对齐、每 3 根连续 5min 聚合；不完整组丢弃 |
| 开发 | 2023-01-01 ～ 2025-01-01，四个连续半年折 |
| 探索审计 | 2025-01-01 ～ 2026-02-28 16:00 UTC，冻结后一次读取 |
| holdout | ≥2026-05-04；本轮物理读取 **0 行** |
| 成本 | 固定往返 0.2%；不含 funding，不额外模拟超过 20bp 的滑点 |
| 审计校验 | `verification.json`，22/22 checks passed |

复现命令：

```bash
cd /Users/zhangzc/fable-trading
PYTHONPATH=. python3 -m scripts.optimize_btcusdtp_k1k2_independent_timeframes --phase development
PYTHONPATH=. python3 -m scripts.optimize_btcusdtp_k1k2_independent_timeframes --phase audit
PYTHONPATH=. python3 scripts/verify_btcusdtp_k1k2_independent_timeframes.py
PYTHONPATH=. python3 scripts/build_btcusdtp_k1k2_independent_report.py
python3 scripts/md_to_html.py \\
  analysis/p1_btcusdtp_k1k2_15m_5m_independent_research_20260904.md \\
  --out-dir analysis/html
```

为避免覆盖冻结研究产物，独立复现前应复制实验目录并改输出路径；上列命令是原始审计轨迹。真正的
可复现证据是 config/script/source SHA、逐笔 ledger 与独立 verifier，而不是重新覆盖旧文件。

## 风险与诚实声明

- 审计期并非从未见过：前一实验已暴露过该期聚合结果。因此本报告只能叫“冻结探索审计”，不能
  冒充 untouched OOS，也没有消耗 holdout。
- 参数网格是有限的单次坐标搜索，不是全局最优；开发结果本身已失败，不能从局部较优偷换成有效。
- 交易以 OHLC barrier 模拟，同根碰撞保守按止损；没有 tick 顺序、资金费率与额外滑点。
- “止损后后来触 3R”是反事实诊断线索，不是可获得收益；新止损会改变风险金额、成本比与路径结果，
  必须重新完整回测。
- 所有收益表都同时给出固定 20bp 成本与匹配随机对照。AUC 只用于检查 score 排序，不是成功标准。
- 结果已拒绝：`training_eligible=false`、`production_eligible=false`，不改 Pine、不 promote、不下单。
"""
    return report


def main() -> None:
    config = load_json(EXPERIMENT / "config.json")
    selection = load_json(RESULTS / "selection_receipt.json")
    audit = pd.read_csv(RESULTS / "audit_metrics.csv")
    predecessor = pd.read_csv(PREDECESSOR)
    start = pd.Timestamp(config["window"]["audit_start_inclusive"])
    end = pd.Timestamp(config["window"]["audit_end_exclusive"])
    audit_months = float((end - start).total_seconds() / 86400.0 / 30.4375)

    comparison = build_strategy_comparison(audit, predecessor, audit_months)
    failures, continuation = build_failure_mechanics()
    risk_bins = build_risk_bins()
    features = build_feature_stability()

    comparison.to_csv(RESULTS / "strategy_comparison.csv", index=False)
    failures.to_csv(RESULTS / "failure_mechanics.csv", index=False)
    continuation.to_csv(RESULTS / "post_stop_continuation.csv", index=False)
    risk_bins.to_csv(RESULTS / "risk_bins.csv", index=False)
    features.to_csv(RESULTS / "feature_stability.csv", index=False)

    plot_density_and_expectancy(comparison)
    plot_ranking(audit)
    plot_failure_mechanics(failures, continuation)
    plot_risk_bins(risk_bins)
    report = make_report(
        config,
        selection,
        audit,
        comparison,
        failures,
        continuation,
        risk_bins,
        features,
    )
    REPORT.write_text(report, encoding="utf-8")
    receipt_files = [
        REPORT,
        RESULTS / "strategy_comparison.csv",
        RESULTS / "failure_mechanics.csv",
        RESULTS / "post_stop_continuation.csv",
        RESULTS / "risk_bins.csv",
        RESULTS / "feature_stability.csv",
        RESULTS / "independent_density_expectancy.png",
        RESULTS / "independent_score_ranking.png",
        RESULTS / "independent_failure_mechanics.png",
        RESULTS / "independent_risk_bins.png",
        RESULTS / "verification.json",
    ]
    receipt = {
        "experiment_id": config["experiment_id"],
        "status": "rejected",
        "holdout_rows_read": 0,
        "audit_window_pristine": False,
        "training_eligible": False,
        "production_eligible": False,
        "files": {
            str(path.relative_to(PROJECT)): {
                "sha256": sha256(path),
                "size_bytes": path.stat().st_size,
            }
            for path in receipt_files
        },
    }
    (RESULTS / "report_receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {REPORT.relative_to(PROJECT)}")
    for name in (
        "strategy_comparison.csv",
        "failure_mechanics.csv",
        "post_stop_continuation.csv",
        "risk_bins.csv",
        "feature_stability.csv",
        "independent_density_expectancy.png",
        "independent_score_ranking.png",
        "independent_failure_mechanics.png",
        "independent_risk_bins.png",
        "report_receipt.json",
    ):
        print(f"wrote {(RESULTS / name).relative_to(PROJECT)}")


if __name__ == "__main__":
    main()
