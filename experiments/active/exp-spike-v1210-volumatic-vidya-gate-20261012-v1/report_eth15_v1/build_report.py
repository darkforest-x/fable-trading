"""Build the ETH 15m technical report from the saved V12.9 and VIDYA ledgers.

Inputs are the frozen experiment config, result summary, candidate diagnostics,
trade ledgers, and matched-control ledger. All transformations are deterministic.
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPORT_DIR = Path(__file__).resolve().parent
EXP_DIR = REPORT_DIR.parent
REPO = EXP_DIR.parents[2]
RESULTS = EXP_DIR / "results_v1"
OUT = REPORT_DIR / "artifact.json"

CONFIG_PATH = EXP_DIR / "config.json"
SUMMARY_PATH = RESULTS / "summary.json"
DIAG_PATH = RESULTS / "candidate_diagnostics.csv"
BASE_PATH = RESULTS / "baseline_trades.csv.gz"
GATE_PATH = RESULTS / "vidya_gate_trades.csv.gz"
CONTROL_PATH = RESULTS / "vidya_gate_controls.csv.gz"

config = json.loads(CONFIG_PATH.read_text())
summary = json.loads(SUMMARY_PATH.read_text())
case_key = "okx:ETH_USDT_SWAP:15m"
case_summary = summary["cases"][case_key]
case_cfg = config["eth_okx"]
start = pd.Timestamp(case_cfg["start"])
split = pd.Timestamp(case_cfg["split"])
end = pd.Timestamp(case_cfg["end"])

parse_dates = ["signal_close", "entry_time", "exit_time"]
def load_trades(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, parse_dates=parse_dates)
    frame = frame[(frame["symbol"] == "ETH_USDT_SWAP") & (frame["timeframe_min"] == 15)]
    frame = frame[(frame["status"] == "closed") & (~frame["censored"].astype(bool))].copy()
    frame["signal_close"] = pd.to_datetime(frame["signal_close"], utc=True)
    frame["entry_time"] = pd.to_datetime(frame["entry_time"], utc=True)
    frame["exit_time"] = pd.to_datetime(frame["exit_time"], utc=True)
    frame = frame[(frame["signal_close"] >= start) & (frame["signal_close"] < end)].copy()
    frame["period"] = np.where(frame["signal_close"] < split, "切分前", "切分后")
    frame["year"] = frame["signal_close"].dt.year.astype(int)
    frame["side_label"] = frame["side"].map({1: "多头", -1: "空头"})
    frame["month"] = frame["signal_close"].dt.strftime("%Y-%m")
    frame["hold_hours"] = (frame["exit_time"] - frame["entry_time"]).dt.total_seconds() / 3600.0
    return frame

baseline = load_trades(BASE_PATH)
gate = load_trades(GATE_PATH)
controls = pd.read_csv(CONTROL_PATH)
controls = controls[(controls["symbol"] == "ETH_USDT_SWAP") & (controls["timeframe_min"] == 15)].copy()
controls["control_net_r"] = pd.to_numeric(controls["control_net_r"], errors="coerce")
controls["matched"] = controls["matched"].astype(bool)
gate = gate.merge(controls[["trade_key", "matched", "control_net_r"]], on="trade_key", how="left", validate="one_to_one")

# Exclude any row that was not successfully matched from control comparisons.
matched_gate = gate[gate["matched"].fillna(False) & gate["control_net_r"].notna()].copy()


def stats(frame: pd.DataFrame) -> dict:
    ordered = frame.sort_values(["exit_time", "trade_key"])
    r = ordered["net_r"].to_numpy(float)
    gross = ordered["gross_r"].to_numpy(float)
    winners = r[r > 0]
    losers = r[r < 0]
    positive = float(winners.sum())
    negative = float(-losers.sum())
    cumulative = np.cumsum(r)
    peaks = np.maximum.accumulate(np.concatenate([[0.0], cumulative]))[1:]
    max_streak = streak = 0
    for value in r:
        if value < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    return {
        "n": int(len(ordered)),
        "wins": int((r > 0).sum()),
        "win_rate": float((r > 0).mean()) if len(r) else None,
        "mean_gross_r": float(np.mean(gross)) if len(gross) else None,
        "mean_cost_r": float(np.mean(gross - r)) if len(r) else None,
        "mean_net_r": float(np.mean(r)) if len(r) else None,
        "sum_net_r": float(np.sum(r)) if len(r) else None,
        "mean_net_bp": float(ordered["net_return"].mean() * 10000) if len(r) else None,
        "profit_factor_r": float(positive / negative) if negative > 0 else None,
        "median_net_r": float(np.median(r)) if len(r) else None,
        "q25_net_r": float(np.quantile(r, 0.25)) if len(r) else None,
        "q75_net_r": float(np.quantile(r, 0.75)) if len(r) else None,
        "median_stop_distance_pct": float(ordered["initial_risk_frac"].median() * 100) if len(r) else None,
        "max_drawdown_r": float(np.min(cumulative - peaks)) if len(r) else None,
        "max_consecutive_losses": int(max_streak),
        "mean_hold_hours": float(ordered["hold_hours"].mean()) if len(r) else None,
        "median_hold_hours": float(ordered["hold_hours"].median()) if len(r) else None,
    }


def period_mask(frame: pd.DataFrame, period: str) -> pd.Series:
    if period == "切分前":
        return (frame["signal_close"] >= start) & (frame["signal_close"] < split)
    if period == "切分后":
        return (frame["signal_close"] >= split) & (frame["signal_close"] < end)
    return (frame["signal_close"] >= start) & (frame["signal_close"] < end)

period_defs = [
    ("切分前", "2024-02 至 2025-10-09", "before_split"),
    ("切分后", "2025-10-10 至 2026-10-09", "from_split"),
    ("全样本", "2024-02 至 2026-10-09", "full"),
]
period_rows = []
period_chart_rows = []
control_rows = []
for label, date_label, summary_key in period_defs:
    b = stats(baseline[period_mask(baseline, label)])
    g = stats(gate[period_mask(gate, label)])
    gs = case_summary["vidya_gate"]["periods"][summary_key]
    control_mean = float(gs["control_mean_net_r"])
    ci = gs["excess_month_block_ci95"]
    row = {
        "period": label,
        "date_scope": date_label,
        "base_n": b["n"],
        "gate_n": g["n"],
        "base_win_rate": b["win_rate"],
        "gate_win_rate": g["win_rate"],
        "base_mean_net_r": b["mean_net_r"],
        "gate_mean_net_r": g["mean_net_r"],
        "base_sum_net_r": b["sum_net_r"],
        "gate_sum_net_r": g["sum_net_r"],
        "base_profit_factor": b["profit_factor_r"],
        "gate_profit_factor": g["profit_factor_r"],
        "gate_mean_gross_r": g["mean_gross_r"],
        "gate_mean_cost_r": g["mean_cost_r"],
        "gate_mean_net_bp": g["mean_net_bp"],
        "gate_control_n": int(gs["matched_controls"]),
        "gate_control_mean_net_r": control_mean,
        "gate_excess_mean_r": float(gs["excess_mean_r"]),
        "gate_excess_ci_low": float(ci[0]),
        "gate_excess_ci_high": float(ci[1]),
        "gate_excess_p": float(gs["excess_month_block_p"]),
        "base_max_drawdown_r": b["max_drawdown_r"],
        "gate_max_drawdown_r": g["max_drawdown_r"],
        "base_max_consecutive_losses": b["max_consecutive_losses"],
        "gate_max_consecutive_losses": g["max_consecutive_losses"],
    }
    period_rows.append(row)
    period_chart_rows.extend([
        {"period": label, "period_date": date_label, "series": "V12.9 基线", "mean_net_r": b["mean_net_r"], "n": b["n"]},
        {"period": label, "period_date": date_label, "series": "VIDYA 过滤", "mean_net_r": g["mean_net_r"], "n": g["n"]},
        {"period": label, "period_date": date_label, "series": "过滤组同状态随机对照", "mean_net_r": control_mean, "n": int(gs["matched_controls"])},
    ])
    control_rows.append({
        "period": label,
        "date_scope": date_label,
        "gate_n": g["n"],
        "gate_mean_net_r": g["mean_net_r"],
        "control_n": int(gs["matched_controls"]),
        "control_mean_net_r": control_mean,
        "excess_mean_r": float(gs["excess_mean_r"]),
        "ci_low": float(ci[0]),
        "ci_high": float(ci[1]),
        "p_value": float(gs["excess_month_block_p"]),
    })

# Check that the recomputed raw-ledger values reproduce the saved summary.
for variant_name, frame in (("baseline", baseline), ("vidya_gate", gate)):
    expected = case_summary[variant_name]
    full = stats(frame)
    assert full["n"] == expected["closed"]
    for field, summary_field in (("mean_net_r", "mean_net_r"), ("sum_net_r", "sum_net_r"), ("profit_factor_r", "profit_factor_r")):
        exp_value = expected["periods"]["full"][summary_field]
        assert math.isclose(full[field], float(exp_value), rel_tol=1e-10, abs_tol=1e-10), (variant_name, field, full[field], exp_value)

# Direction detail: all-sample and post-split segments, by strategy and side.
side_rows = []
for period in ("全样本", "切分后"):
    for variant, frame in (("V12.9 基线", baseline), ("VIDYA 过滤", gate)):
        subset = frame[period_mask(frame, period)]
        for side in ("多头", "空头"):
            group = subset[subset["side_label"] == side]
            m = stats(group)
            side_rows.append({
                "period": period, "side": side, "variant": variant,
                "n": m["n"], "win_rate": m["win_rate"],
                "mean_net_r": m["mean_net_r"], "sum_net_r": m["sum_net_r"],
                "profit_factor_r": m["profit_factor_r"],
            })

# Calendar-year results are grouped by signal close; 2026 is partial through Oct 10.
year_rows = []
for year in sorted(set(baseline["year"]) | set(gate["year"])):
    for variant, frame in (("V12.9 基线", baseline), ("VIDYA 过滤", gate)):
        m = stats(frame[frame["year"] == int(year)])
        year_rows.append({"year": int(year), "variant": variant, "n": m["n"], "win_rate": m["win_rate"],
                          "mean_net_r": m["mean_net_r"], "sum_net_r": m["sum_net_r"],
                          "profit_factor_r": m["profit_factor_r"]})

# Exit decomposition highlights how a low win rate can coexist with a positive mean.
exit_labels = {"initial_stop": "初始止损", "opposite_v6_next_open": "反向信号下一开盘退出", "trailing_stop": "移动止损"}
exit_rows = []
for variant, frame in (("V12.9 基线", baseline), ("VIDYA 过滤", gate)):
    for reason in ("initial_stop", "opposite_v6_next_open", "trailing_stop"):
        group = frame[frame["exit_reason"] == reason]
        m = stats(group)
        exit_rows.append({"variant": variant, "exit_reason": exit_labels.get(reason, reason),
                          "n": m["n"], "share": m["n"] / len(frame) if len(frame) else None,
                          "win_rate": m["win_rate"], "mean_net_r": m["mean_net_r"],
                          "sum_net_r": m["sum_net_r"]})

# Monthly cumulative R is grouped by signal month, not a compounded equity curve.
month_start = pd.Period(start.strftime("%Y-%m"), freq="M")
month_end = pd.Period((end - pd.Timedelta(seconds=1)).strftime("%Y-%m"), freq="M")
months = [str(m) for m in pd.period_range(month_start, month_end, freq="M")]
def grouped_month(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["month", "n", "sum_net_r", "mean_net_r"])
    return frame.groupby("month").agg(n=("net_r", "size"), sum_net_r=("net_r", "sum"), mean_net_r=("net_r", "mean")).reset_index()
base_month = grouped_month(baseline).set_index("month")
gate_month = grouped_month(gate).set_index("month")
control_frame = matched_gate.copy()
control_frame["month"] = control_frame["signal_close"].dt.strftime("%Y-%m")
control_month = control_frame.groupby("month").agg(n=("control_net_r", "size"), sum_control_net_r=("control_net_r", "sum"), mean_control_net_r=("control_net_r", "mean"))
monthly_rows = []
monthly_chart_rows = []
cum_base = cum_gate = cum_control = 0.0
for month in months:
    b_n = int(base_month.loc[month, "n"]) if month in base_month.index else 0
    b_sum = float(base_month.loc[month, "sum_net_r"]) if month in base_month.index else 0.0
    g_n = int(gate_month.loc[month, "n"]) if month in gate_month.index else 0
    g_sum = float(gate_month.loc[month, "sum_net_r"]) if month in gate_month.index else 0.0
    c_n = int(control_month.loc[month, "n"]) if month in control_month.index else 0
    c_sum = float(control_month.loc[month, "sum_control_net_r"]) if month in control_month.index else 0.0
    c_mean = float(control_month.loc[month, "mean_control_net_r"]) if month in control_month.index else None
    cum_base += b_sum
    cum_gate += g_sum
    cum_control += c_sum
    month_date = f"{month}-01"
    monthly_rows.append({"month": month_date, "baseline_n": b_n, "baseline_month_sum_r": b_sum,
                         "gate_n": g_n, "gate_month_sum_r": g_sum, "control_n": c_n,
                         "control_month_mean_r": c_mean, "control_month_sum_r": c_sum,
                         "baseline_cumulative_r": cum_base, "gate_cumulative_r": cum_gate,
                         "control_cumulative_r": cum_control})
    for series, value, n in (("V12.9 基线", cum_base, b_n), ("VIDYA 过滤", cum_gate, g_n), ("过滤组同状态随机对照", cum_control, c_n)):
        monthly_chart_rows.append({"month": month_date, "series": series, "cumulative_net_r": value,
                                   "month_net_r": b_sum if series == "V12.9 基线" else g_sum if series == "VIDYA 过滤" else c_sum,
                                   "trades": n})

# Distribution of net R (a win is strictly net_r > 0 after cost).
net_r = gate["net_r"].to_numpy(float)
bin_edges = [-np.inf, -1.5, -1.0, 0.0, 1.0, 3.0, np.inf]
bin_labels = ["≤ −1.5R", "(−1.5R, −1R]", "(−1R, 0R]", "(0R, 1R]", "(1R, 3R]", "> 3R"]
cat = pd.cut(gate["net_r"], bins=bin_edges, labels=bin_labels, right=True, include_lowest=True)
dist_counts = cat.value_counts(sort=False)
distribution_rows = []
for label in bin_labels:
    count = int(dist_counts.get(label, 0))
    distribution_rows.append({"net_r_bin": label, "trade_count": count, "share": count / len(gate) if len(gate) else None})
positive = gate.loc[gate["net_r"] > 0, "net_r"].sort_values(ascending=False)
concentration = {
    "positive_trades": int((gate["net_r"] > 0).sum()),
    "median_net_r": float(gate["net_r"].median()),
    "q25_net_r": float(gate["net_r"].quantile(0.25)),
    "q75_net_r": float(gate["net_r"].quantile(0.75)),
    "top_1_share_positive_r": float(positive.head(1).sum() / positive.sum()),
    "top_5_share_positive_r": float(positive.head(5).sum() / positive.sum()),
    "top_10_share_positive_r": float(positive.head(10).sum() / positive.sum()),
    "largest_winner_r": float(positive.iloc[0]),
}

# Retain source evidence and report calculations as a reproducible input bundle.
source_paths = [CONFIG_PATH, SUMMARY_PATH, DIAG_PATH, BASE_PATH, GATE_PATH, CONTROL_PATH]
source_bundle = {
    "id": "eth15_report_analysis",
    "label": "ETH 15m · 冻结回测结果与报告派生脚本",
    "path": REPORT_DIR.relative_to(REPO).joinpath("build_report.py").as_posix(),
    "query": {
        "engine": "Python 3 / pandas",
        "language": "python",
        "description": "报告指标和图表由 build_report.py 从实验配置、已保存摘要、候选诊断、基线成交、VIDYA 过滤成交和匹配随机对照确定性计算。逐笔字段按信号收盘时段、信号年份、方向和出场原因切片；月度累计R按信号月求和。p值与区间沿用冻结 summary.json 中的日历月区块符号翻转结果。",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "tables_used": [p.relative_to(REPO).as_posix() for p in source_paths],
        "metric_definitions": [
            "Win rate = share of closed trades with net_r > 0 after the fixed 0.2% round-trip cost.",
            "Mean net R = arithmetic mean of net return divided by initial stop distance; sum net R is additive event R, not compounded account return.",
            "Profit factor R = sum of positive net R divided by the absolute sum of negative net R.",
            "Matched controls are deterministic random entries matched on symbol, direction, UTC week, fold, ATR/close bin, and the same confirmed VIDYA state; same exit engine and cost.",
        ],
    },
}

summary_full = case_summary["vidya_gate"]["periods"]["full"]
base_full = case_summary["baseline"]["periods"]["full"]
quality_diag = pd.read_csv(DIAG_PATH)
quality_diag = quality_diag[(quality_diag["symbol"] == "ETH_USDT_SWAP") & (quality_diag["timeframe_min"] == 15)].iloc[0]
quality = {
    "data_gaps": int(quality_diag["data_gaps"]),
    "bars": int(quality_diag["bars"]),
    "raw_candidates": int(quality_diag["raw_candidates"]),
    "admitted_candidates": int(quality_diag["admitted_candidates"]),
    "rejected_opposite": int(quality_diag["rejected_opposite"]),
    "baseline_trades": int(quality_diag["baseline_trades_closed"]),
    "gate_trades": int(quality_diag["gated_trades_closed"]),
    "saved_v129_parity": str(quality_diag["saved_v129_parity"]),
}

# Table fields are converted to plain JSON scalars; NaN/inf become null.
def clean(value):
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return value

def rows_clean(rows):
    return [{k: clean(v) for k, v in row.items()} for row in rows]

period_rows = rows_clean(period_rows)
period_chart_rows = rows_clean(period_chart_rows)
control_rows = rows_clean(control_rows)
side_rows = rows_clean(side_rows)
year_rows = rows_clean(year_rows)
exit_rows = rows_clean(exit_rows)
monthly_rows = rows_clean(monthly_rows)
monthly_chart_rows = rows_clean(monthly_chart_rows)
distribution_rows = rows_clean(distribution_rows)

# Materialize each bounded chart snapshot with the exact SQL retained in source
# metadata. The SQL is executed here, after reviewed rows have been derived from
# the experiment's saved result files.
def sql_literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)):
        return repr(float(value)) if math.isfinite(float(value)) else "NULL"
    return "'" + str(value).replace("'", "''") + "'"

def materialize_chart_rows(rows):
    fields = list(rows[0].keys())
    cte_fields = ["row_order"] + fields
    values_sql = ",\n".join(
        "(" + ", ".join([str(index)] + [sql_literal(row[field]) for field in fields]) + ")"
        for index, row in enumerate(rows)
    )
    sql = (
        f"WITH chart_source ({', '.join(cte_fields)}) AS (VALUES\n{values_sql}\n)\n"
        f"SELECT {', '.join(fields)} FROM chart_source ORDER BY row_order"
    )
    conn = sqlite3.connect(":memory:")
    try:
        cursor = conn.execute(sql)
        result = cursor.fetchall()
        columns = [item[0] for item in cursor.description]
    finally:
        conn.close()
    return sql, [dict(zip(columns, row)) for row in result]

period_chart_sql, period_chart_rows = materialize_chart_rows(period_chart_rows)
monthly_chart_sql, monthly_chart_rows = materialize_chart_rows(monthly_chart_rows)
distribution_sql, distribution_rows = materialize_chart_rows(distribution_rows)
period_table_sql, period_rows = materialize_chart_rows(period_rows)
control_table_sql, control_rows = materialize_chart_rows(control_rows)
monthly_table_sql, monthly_rows = materialize_chart_rows(monthly_rows)
direction_table_sql, side_rows = materialize_chart_rows(side_rows)
annual_table_sql, year_rows = materialize_chart_rows(year_rows)
exit_table_sql, exit_rows = materialize_chart_rows(exit_rows)

def chart_source(source_id: str, label: str, query_sql: str) -> dict:
    return {
        "id": source_id,
        "label": label,
        "path": REPORT_DIR.relative_to(REPO).joinpath("build_report.py").as_posix(),
        "query": {
            "engine": "SQLite",
            "language": "sql",
            "sql": query_sql,
            "description": "从 build_report.py 基于冻结实验源文件计算出的审阅后快照行；SQL按该图表所需字段和顺序物化有限快照。",
            "executed_at": datetime.now(timezone.utc).isoformat(),
            "tables_used": [p.relative_to(REPO).as_posix() for p in source_paths],
        },
    }

period_chart_source = chart_source("period_chart_data", "ETH 15m · 分期图表快照", period_chart_sql)
monthly_chart_source = chart_source("monthly_chart_data", "ETH 15m · 月度累计R图表快照", monthly_chart_sql)
distribution_chart_source = chart_source("distribution_chart_data", "ETH 15m · 单笔净R分布图表快照", distribution_sql)
period_table_source = chart_source("period_table_data", "ETH 15m · 分期指标表快照", period_table_sql)
control_table_source = chart_source("control_table_data", "ETH 15m · 匹配对照表快照", control_table_sql)
monthly_table_source = chart_source("monthly_table_data", "ETH 15m · 月度审计表快照", monthly_table_sql)
direction_table_source = chart_source("direction_table_data", "ETH 15m · 多空拆分表快照", direction_table_sql)
annual_table_source = chart_source("annual_table_data", "ETH 15m · 年度拆分表快照", annual_table_sql)
exit_table_source = chart_source("exit_table_data", "ETH 15m · 出场拆分表快照", exit_table_sql)
all_sources = [
    source_bundle, period_chart_source, monthly_chart_source, distribution_chart_source,
    period_table_source, control_table_source, monthly_table_source,
    direction_table_source, annual_table_source, exit_table_source,
]

# Confirm core report facts reproduce from row-level files before packaging.
assert len(matched_gate) == int(summary_full["matched_controls"]) == 142
assert quality["data_gaps"] == 0
assert len(baseline) == 164 and len(gate) == 142

# Build short narrative values from the recomputed ledger.
g_full = stats(gate)
b_full = stats(baseline)
g_before = stats(gate[period_mask(gate, "切分前")])
g_after = stats(gate[period_mask(gate, "切分后")])
y2026 = stats(gate[gate["year"] == 2026])
post_short = stats(gate[(gate["period"] == "切分后") & (gate["side_label"] == "空头")])
post_long = stats(gate[(gate["period"] == "切分后") & (gate["side_label"] == "多头")])
full_control = period_rows[2]

fmt_r = lambda x: f"{x:+.3f}R"
fmt_num = lambda x: f"{x:.3f}"
fmt_pct = lambda x: f"{x * 100:.1f}%"

blocks = [
    {"id": "title", "type": "markdown", "body": "# ETH 15m 回测：V12.10 VIDYA 入场过滤", "layout": "full"},
    {"id": "technical_summary", "type": "markdown", "sourceId": "eth15_report_analysis", "layout": "full", "body": (
        "## 技术摘要\n\n"
        f"在 2024-02 至 2026-10-09 的 ETHUSDT 永续 15m 样本中，VIDYA 方向过滤后有 **{g_full['n']} 笔**闭合交易，平均净收益 **{fmt_r(g_full['mean_net_r'])}/笔**，PF **{g_full['profit_factor_r']:.3f}**，合计 **{fmt_r(g_full['sum_net_r'])}**。相比 V12.9 基线的 {b_full['n']} 笔、{fmt_r(b_full['mean_net_r'])}/笔，过滤后描述性均值提高 {fmt_r(g_full['mean_net_r'] - b_full['mean_net_r'])}。\n\n"
        f"相对同状态随机入场，过滤组全样本超额为 **{fmt_r(full_control['gate_excess_mean_r'])}**（95%区间 {fmt_r(full_control['gate_excess_ci_low'])} 至 {fmt_r(full_control['gate_excess_ci_high'])}，月区块 p={full_control['gate_excess_p']:.4f}）。但切分后收益集中在空头和 2026 年部分行情；过滤相对基线的增量没有做配对显著性检验。因此这是一条值得继续拆解的研究结果，不构成稳定性或实盘准入证明。"
    )},
    {"id": "definitions", "type": "markdown", "sourceId": "eth15_report_analysis", "layout": "full", "body": (
        "## 先看口径：胜率和 R 都扣除了固定成本\n\n"
        "本报告的胜率按净 R > 0 计算；净 R 是每笔净收益除以初始止损距离。PF 是正净 R 总和除以负净 R 绝对值之和。Σ净 R 是逐笔 R 的算术相加，不包含固定账户本金、仓位分配或复利，不能直接读成账户收益率。成本固定为每笔往返 0.2%，因此 5bp 或 20bp 的收益比例不等于同样大小的 R。\n\n"
        "切分点为 2025-10-10 00:00 UTC；切分前从 2024-02-01 起，切分后截至 2026-10-10 10:00 UTC。统计按信号收盘时间归期。2026 是截至 10 月 10 日的部分年度。"
    )},
    {"id": "period_interpretation", "type": "markdown", "sourceId": "eth15_report_analysis", "layout": "full", "body": (
        "## 过滤后均值高于基线，但前后时段差异明显\n\n"
        f"切分前过滤组平均净收益为 {fmt_r(g_before['mean_net_r'])}/笔，累计 {fmt_r(g_before['sum_net_r'])}；切分后为 {fmt_r(g_after['mean_net_r'])}/笔，累计 {fmt_r(g_after['sum_net_r'])}。图中每组比较 V12.9 基线、VIDYA 过滤和过滤组对应的同状态随机对照；随机对照不是 V12.9 的原匹配对照。"
    )},
    {"id": "period_chart", "type": "chart", "chartId": "period_mean", "layout": "full"},
    {"id": "period_table", "type": "table", "tableId": "period_summary", "layout": "full"},
    {"id": "control_interpretation", "type": "markdown", "sourceId": "eth15_report_analysis", "layout": "full", "body": (
        "## 对照优势在切分后更强；基线比较仍只是描述性结果\n\n"
        "对照按同币、同方向、同 UTC 周、同研究折、同 ATR/价格波动桶和同 VIDYA 趋势状态匹配，使用同一 V12.9 出场逻辑与 0.2% 往返成本；每笔过滤交易对应一个确定性随机入场。p 值使用按日历月分块的符号翻转。\n\n"
        "全样本的 95% 超额区间不含零；切分后仍高于零，但 p=0.0163；切分前区间跨零。ETH 15m 是单独报告的参考市场，不是本实验预注册的山寨币池主要验收项，也不应用单个 ETH p 值改写总体结论。"
    )},
    {"id": "control_table", "type": "table", "tableId": "control_summary", "layout": "full"},
    {"id": "monthly_interpretation", "type": "markdown", "sourceId": "eth15_report_analysis", "layout": "full", "body": (
        "## 2026 部分年度贡献了过滤组大部分累计 R\n\n"
        f"按信号年份，过滤组 2026 年截至 10 月 10 日共 {y2026['n']} 笔，平均 {fmt_r(y2026['mean_net_r'])}/笔、累计 {fmt_r(y2026['sum_net_r'])}。月度图按信号月累计逐笔净 R，用来定位收益发生时段；它不是按资金曲线、复利或跨周期组合生成的账户净值。"
    )},
    {"id": "monthly_chart", "type": "chart", "chartId": "monthly_cumulative", "layout": "full"},
    {"id": "monthly_table", "type": "table", "tableId": "monthly_detail", "layout": "full"},
    {"id": "tail_interpretation", "type": "markdown", "sourceId": "eth15_report_analysis", "layout": "full", "body": (
        "## 15m 盈利来自低频大赢家；典型单笔仍然亏损\n\n"
        f"过滤组胜率为 {fmt_pct(g_full['win_rate'])}，但中位净 R 为 {fmt_r(concentration['median_net_r'])}，四分位范围 {fmt_r(concentration['q25_net_r'])} 至 {fmt_r(concentration['q75_net_r'])}。最好的 5 笔贡献了全部正净 R 的 {concentration['top_5_share_positive_r'] * 100:.1f}%，最大单笔为 {fmt_r(concentration['largest_winner_r'])}。这种右尾结构解释了均值为正、同时多数单笔仍亏损的现象。"
    )},
    {"id": "distribution_chart", "type": "chart", "chartId": "net_r_distribution", "layout": "full"},
    {"id": "direction_interpretation", "type": "markdown", "sourceId": "eth15_report_analysis", "layout": "full", "body": (
        "## 切分后正收益由空头贡献，多头仍是负值\n\n"
        f"切分后过滤组空头 {post_short['n']} 笔，平均 {fmt_r(post_short['mean_net_r'])}/笔、累计 {fmt_r(post_short['sum_net_r'])}；多头 {post_long['n']} 笔，平均 {fmt_r(post_long['mean_net_r'])}/笔、累计 {fmt_r(post_long['sum_net_r'])}。下面的方向表同时列出全样本和切分后基线，方向内样本数不同，不应把组间差异读成经检验的方向因果效应。"
    )},
    {"id": "direction_table", "type": "table", "tableId": "direction_detail", "layout": "full"},
    {"id": "annual_interpretation", "type": "markdown", "sourceId": "eth15_report_analysis", "layout": "full", "body": (
        "## 年度切分显示收益并非逐年持续\n\n"
        "按信号年份归集时，2024 和 2025 与 2026 部分年度的方向不同。年度是描述性分组，没有单独重新估计区块 p 值；不能把 2026 的局部表现外推为跨周期规律。"
    )},
    {"id": "annual_table", "type": "table", "tableId": "annual_detail", "layout": "full"},
    {"id": "exit_interpretation", "type": "markdown", "sourceId": "eth15_report_analysis", "layout": "full", "body": (
        "## 移动止损赢单抵消了初始止损和反向退出的亏损\n\n"
        f"过滤组平均初始止损距离为 {g_full['median_stop_distance_pct']:.3f}% 的价格，平均持仓 {g_full['mean_hold_hours']:.1f} 小时、中位 {g_full['median_hold_hours']:.1f} 小时。最长连续亏损为 {g_full['max_consecutive_losses']} 笔，按逐笔净 R、退出时间排序的最大回撤为 {fmt_r(g_full['max_drawdown_r'])}；两者都不是固定资金账户的风险曲线。"
    )},
    {"id": "exit_table", "type": "table", "tableId": "exit_detail", "layout": "full"},
    {"id": "methods_limitations", "type": "markdown", "sourceId": "eth15_report_analysis", "layout": "full", "body": (
        "## 方法、校验和限制\n\n"
        "过滤条件只看 ETH 当前 15m 图上已确认的 VIDYA 状态：多头信号要求上行状态，空头信号要求下行状态。保持 V12.9 普通信号、下一根开盘成交、初始止损、追踪止损、反向退出和成本设置不变；拒绝的信号不会占用串行仓位。\n\n"
        f"诊断文件记录 {quality['bars']:,} 根 15m bar、数据缺口 {quality['data_gaps']}；{quality['raw_candidates']} 个原始候选中 {quality['admitted_candidates']} 个方向匹配，最终基线 {quality['baseline_trades']} 笔、过滤账本 {quality['gate_trades']} 笔。V12.9 保存账本逐笔一致性记录为：{quality['saved_v129_parity']}。\n\n"
        "控制只抽一笔确定性随机入场，匹配后仍可能共享月份或行情段；月区块方法考虑了月份聚集，但不等于独立样本，也没有检验过滤策略与 V12.9 基线差值的配对区间。ETH 被指定为参考项；整体决策仍需服从 20 个山寨币池的预注册门槛。未建模资金费率、冲击成本随行情变化、组合级资金占用和杠杆后的权益回撤。"
    )},
    {"id": "next_step", "type": "markdown", "sourceId": "eth15_report_analysis", "layout": "full", "body": (
        "## 建议：保留为研究结果，先验证方向与时段稳定性\n\n"
        "下一步保持 VIDYA 参数和退出规则冻结，单独做 ETH 15m 的方向分层稳健性分析：比较多头、空头及切分前后相对同状态控制的净 R 与区块区间。现有结果不支持直接 promote 或进入实盘。"
    )},
]

manifest = {
    "version": 1,
    "surface": "report",
    "title": "ETH 15m 回测：V12.10 VIDYA 入场过滤",
    "description": "OKX ETHUSDT 永续 15 分钟；冻结 V12.9 普通信号加同周期 Volumatic VIDYA 趋势方向入场门。",
    "generatedAt": datetime.now(timezone.utc).isoformat(),
    "filters": [],
    "cards": [],
    "charts": [
        {
            "id": "period_mean", "type": "bar", "title": "分期平均净 R 对比",
            "subtitle": "ETH 15m · 每笔已扣 0.2% 往返成本 · 过滤组随机对照使用 VIDYA 状态匹配",
            "showDescription": True, "intent": "comparison", "question": "VIDYA 过滤在切分前后是否改善 ETH 15m 每笔净 R？",
            "rationale": "Three discrete study periods make a grouped bar comparison more honest than a trend line; a zero reference separates profitable from losing means.",
            "dataset": "period_chart", "sourceId": "period_chart_data", "layout": "full",
            "palette": {"kind": "categorical", "name": "blueGold"},
            "labels": {"values": "all"},
            "settings": {"groupMode": "grouped", "orientation": "vertical", "sort": "none", "categoryLabelPolicy": "wrap"},
            "referenceLines": [{"axis": "y", "value": 0, "color": "neutral", "lineStyle": "solid", "label": "0 R"}],
            "valueFormat": "number", "unit": "R",
            "encodings": {
                "x": {"field": "period", "type": "ordinal", "label": "信号时段"},
                "y": {"field": "mean_net_r", "type": "quantitative", "label": "净 R / 笔", "format": "number", "unit": "R"},
                "color": {"field": "series", "type": "nominal", "label": "账本"},
                "tooltip": [{"field": "period_date", "type": "text", "label": "日期范围"}, {"field": "n", "type": "quantitative", "label": "交易数"}, {"field": "mean_net_r", "type": "quantitative", "label": "净 R / 笔", "unit": "R"}]
            }
        },
        {
            "id": "monthly_cumulative", "type": "line", "title": "按信号月累计的逐笔净 R",
            "subtitle": "2024年2月至2026年10月 · 月份按信号收盘归属 · 非账户权益曲线",
            "showDescription": True, "intent": "trend", "question": "ETH 15m 累计净 R 在什么时段形成？",
            "rationale": "Monthly grain supplies enough observations to reveal when the additive per-trade R result accumulated; tooltip counts show monthly trade volume.",
            "dataset": "monthly_cumulative", "sourceId": "monthly_chart_data", "layout": "full",
            "palette": {"kind": "categorical", "name": "blueGold"},
            "legend": {"position": "bottom", "sort": "spec", "title": "账本"},
            "settings": {"showPoints": "never", "sort": "none"},
            "referenceLines": [{"axis": "y", "value": 0, "color": "neutral", "lineStyle": "solid", "label": "0 R"}],
            "valueFormat": "number", "unit": "R",
            "encodings": {
                "x": {"field": "month", "type": "temporal", "label": "信号月份"},
                "y": {"field": "cumulative_net_r", "type": "quantitative", "label": "累计逐笔净 R", "format": "number", "unit": "R"},
                "color": {"field": "series", "type": "nominal", "label": "账本"},
                "tooltip": [{"field": "month_net_r", "type": "quantitative", "label": "当月逐笔净 R", "unit": "R"}, {"field": "trades", "type": "quantitative", "label": "当月交易数"}]
            }
        },
        {
            "id": "net_r_distribution", "type": "bar", "title": "VIDYA 过滤后单笔净 R 分布",
            "subtitle": "142笔闭合交易 · 净 R 已扣成本 · 胜单定义为净 R 严格大于 0",
            "showDescription": True, "intent": "distribution", "question": "整体正均值是否由多数小赢，还是少数大赢家拉动？",
            "rationale": "Six ordered outcome bins show the loss mass and the positive tail without replacing exact quantiles in the narrative.",
            "dataset": "net_r_distribution", "sourceId": "distribution_chart_data", "layout": "full",
            "palette": {"kind": "sequential", "name": "blue"},
            "labels": {"values": "all"},
            "settings": {"orientation": "vertical", "sort": "none", "categoryLabelPolicy": "wrap"},
            "valueFormat": "number", "unit": "trades",
            "encodings": {
                "x": {"field": "net_r_bin", "type": "ordinal", "label": "净 R 区间"},
                "y": {"field": "trade_count", "type": "quantitative", "label": "交易数", "format": "number"},
                "tooltip": [{"field": "trade_count", "type": "quantitative", "label": "交易数"}, {"field": "share", "type": "quantitative", "label": "占比", "format": "percent"}]
            }
        }
    ],
    "tables": [
        {"id": "period_summary", "title": "基线与过滤账本分期指标", "subtitle": "按信号收盘时间分期；收益单位为每笔净 R，累计 R 为非复利加总。", "showDescription": True, "dataset": "period_summary", "sourceId": "period_table_data", "layout": "full", "defaultSort": {"field": "gate_mean_net_r", "direction": "desc"}, "density": "spacious", "columns": [
            {"field": "period", "label": "时段", "type": "text"}, {"field": "base_n", "label": "V12.9 笔数", "type": "number"}, {"field": "gate_n", "label": "过滤后笔数", "type": "number"},
            {"field": "base_win_rate", "label": "基线胜率", "format": "percent"}, {"field": "gate_win_rate", "label": "过滤后胜率", "format": "percent"},
            {"field": "base_mean_net_r", "label": "基线净 R/笔", "type": "number", "movement": True}, {"field": "gate_mean_net_r", "label": "过滤后净 R/笔", "type": "number", "movement": True},
            {"field": "base_sum_net_r", "label": "基线Σ净 R", "type": "number", "movement": True}, {"field": "gate_sum_net_r", "label": "过滤后Σ净 R", "type": "number", "movement": True},
            {"field": "base_profit_factor", "label": "基线 PF", "type": "number"}, {"field": "gate_profit_factor", "label": "过滤后 PF", "type": "number"},
        ]},
        {"id": "control_summary", "title": "过滤信号与同状态随机对照", "subtitle": "对照同币、方向、UTC周、折、波动桶及VIDYA状态；p值按日历月区块符号翻转。", "showDescription": True, "dataset": "control_summary", "sourceId": "control_table_data", "layout": "full", "defaultSort": {"field": "excess_mean_r", "direction": "desc"}, "density": "spacious", "columns": [
            {"field": "period", "label": "时段", "type": "text"}, {"field": "gate_n", "label": "过滤交易数", "type": "number"}, {"field": "gate_mean_net_r", "label": "过滤净 R/笔", "type": "number", "movement": True},
            {"field": "control_mean_net_r", "label": "对照净 R/笔", "type": "number", "movement": True}, {"field": "excess_mean_r", "label": "超额 R/笔", "type": "number", "movement": True},
            {"field": "ci_low", "label": "95%区间下限", "type": "number", "movement": True}, {"field": "ci_high", "label": "95%区间上限", "type": "number", "movement": True}, {"field": "p_value", "label": "月区块 p 值", "type": "number"},
        ]},
        {"id": "monthly_detail", "title": "逐月收益审计表", "subtitle": "按信号月汇总笔数和净 R；控制组与 VIDYA 过滤交易配对，不等同于账户权益曲线。", "showDescription": True, "dataset": "monthly_detail", "sourceId": "monthly_table_data", "layout": "full", "defaultSort": {"field": "month", "direction": "asc"}, "density": "dense", "columns": [
            {"field": "month", "label": "信号月份", "type": "date"}, {"field": "baseline_n", "label": "基线笔数", "type": "number"}, {"field": "baseline_month_sum_r", "label": "基线当月ΣR", "type": "number", "movement": True},
            {"field": "gate_n", "label": "过滤笔数", "type": "number"}, {"field": "gate_month_sum_r", "label": "过滤当月ΣR", "type": "number", "movement": True},
            {"field": "control_n", "label": "匹配对照笔数", "type": "number"}, {"field": "control_month_mean_r", "label": "对照月均 R/笔", "type": "number", "movement": True},
            {"field": "gate_cumulative_r", "label": "过滤累计ΣR", "type": "number", "movement": True},
        ]},
        {"id": "direction_detail", "title": "方向拆分：全样本与切分后", "subtitle": "多空分组为描述性统计，方向间样本不同且未单独做显著性检验。", "showDescription": True, "dataset": "direction_detail", "sourceId": "direction_table_data", "layout": "full", "defaultSort": {"field": "mean_net_r", "direction": "desc"}, "density": "dense", "columns": [
            {"field": "period", "label": "时段", "type": "text"}, {"field": "side", "label": "方向", "type": "text"}, {"field": "variant", "label": "账本", "type": "text"}, {"field": "n", "label": "笔数", "type": "number"},
            {"field": "win_rate", "label": "胜率", "format": "percent"}, {"field": "mean_net_r", "label": "净 R/笔", "type": "number", "movement": True},
            {"field": "sum_net_r", "label": "Σ净 R", "type": "number", "movement": True}, {"field": "profit_factor_r", "label": "PF", "type": "number"},
        ]},
        {"id": "annual_detail", "title": "按信号年份拆分", "subtitle": "2026数据截至10月10日；年度样本未单独计算区块置信区间。", "showDescription": True, "dataset": "annual_detail", "sourceId": "annual_table_data", "layout": "full", "defaultSort": {"field": "year", "direction": "asc"}, "density": "dense", "columns": [
            {"field": "year", "label": "年份", "type": "number"}, {"field": "variant", "label": "账本", "type": "text"}, {"field": "n", "label": "笔数", "type": "number"},
            {"field": "win_rate", "label": "胜率", "format": "percent"}, {"field": "mean_net_r", "label": "净 R/笔", "type": "number", "movement": True},
            {"field": "sum_net_r", "label": "Σ净 R", "type": "number", "movement": True}, {"field": "profit_factor_r", "label": "PF", "type": "number"},
        ]},
        {"id": "exit_detail", "title": "出场原因拆分", "subtitle": "平均净 R 已扣往返成本；各组交易数不同。", "showDescription": True, "dataset": "exit_detail", "sourceId": "exit_table_data", "layout": "full", "defaultSort": {"field": "sum_net_r", "direction": "desc"}, "density": "dense", "columns": [
            {"field": "variant", "label": "账本", "type": "text"}, {"field": "exit_reason", "label": "出场原因", "type": "text"}, {"field": "n", "label": "笔数", "type": "number"},
            {"field": "share", "label": "占该账本比例", "format": "percent"}, {"field": "win_rate", "label": "胜率", "format": "percent"},
            {"field": "mean_net_r", "label": "净 R/笔", "type": "number", "movement": True}, {"field": "sum_net_r", "label": "Σ净 R", "type": "number", "movement": True},
        ]},
    ],
    "sources": all_sources,
    "blocks": blocks,
}

artifact = {
    "surface": "report",
    "manifest": manifest,
    "snapshot": {
        "version": 1,
        "generatedAt": manifest["generatedAt"],
        "status": "ready",
        "datasets": {
            "period_summary": period_rows,
            "period_chart": period_chart_rows,
            "control_summary": control_rows,
            "monthly_detail": monthly_rows,
            "monthly_cumulative": monthly_chart_rows,
            "net_r_distribution": distribution_rows,
            "direction_detail": side_rows,
            "annual_detail": year_rows,
            "exit_detail": exit_rows,
        },
        "accessIssues": [],
    },
    "sources": all_sources,
}
OUT.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
print(f"Wrote {OUT}")
print(f"ETH 15m raw ledger rows: baseline={len(baseline)}, gate={len(gate)}, matched_controls={len(matched_gate)}")
print(f"Full gate mean net R={g_full['mean_net_r']:.6f}; sum={g_full['sum_net_r']:.6f}; PF={g_full['profit_factor_r']:.6f}")
print(f"Full gate vs matched control: excess={full_control['gate_excess_mean_r']:.6f}; p={full_control['gate_excess_p']:.4f}")
print(f"Artifacts: {len(manifest['charts'])} charts, {len(manifest['tables'])} tables, {len(monthly_rows)} months")
