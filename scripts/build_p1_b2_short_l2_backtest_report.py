#!/usr/bin/env python3
"""Build the audited Markdown and portable-artifact inputs for the B2 replay.

Inputs are the immutable summary and row-level CSVs produced by
``backtest_local_signal_v2_b2_short_pool.py``.  This report builder performs
only deterministic aggregation; it does not run inference, select thresholds,
read holdout data, or alter any model/production configuration.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
PREFIX = PROJECT / "analysis/output/p1_b2_short_l2_backtest_20260811"
SUMMARY_PATH = PREFIX.with_suffix(".json")
ROWS_PATH = PREFIX.with_name(PREFIX.name + "_rows.csv")
SELECTED_PATH = PREFIX.with_name(PREFIX.name + "_selected.csv")
MATCHED_PATH = PREFIX.with_name(PREFIX.name + "_matched.csv")
REPORT_DIR = PROJECT / "analysis/output/p1_b2_short_l2_backtest_report_20260811"
ARTIFACT_PATH = REPORT_DIR / "artifact.json"
DAILY_PATH = REPORT_DIR / "daily.csv"
SYMBOL_PATH = REPORT_DIR / "symbol.csv"
MD_PATH = PROJECT / "analysis/p1_b2_short_l2_backtest_20260811.md"


def pf(values: pd.Series) -> float | None:
    values = pd.to_numeric(values, errors="coerce").dropna()
    gains = float(values[values > 0].sum())
    losses = float(values[values < 0].sum())
    return gains / -losses if losses < 0 else None


def make_rollups(selected: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Create auditable daily and symbol rollups from selected row-level data."""
    work = selected.copy()
    work["net10"] = work["net_ret_swap_taker"]
    work["net20"] = work["gross_ret"] - 0.002
    work["utc_date"] = work["signal_time"].dt.strftime("%Y-%m-%d")

    daily_rows = []
    cumulative = 0.0
    peak = 0.0
    for date, frame in work.groupby("utc_date", sort=True):
        daily_total = float(frame["net20"].sum())
        cumulative += daily_total
        peak = max(peak, cumulative)
        daily_rows.append(
            {
                "utc_date": date,
                "n": int(len(frame)),
                "mean_gross_bp": float(frame["gross_ret"].mean() * 1e4),
                "mean_net10_bp": float(frame["net10"].mean() * 1e4),
                "mean_net20_bp": float(frame["net20"].mean() * 1e4),
                "win_rate_net20": float((frame["net20"] > 0).mean()),
                "profit_factor_net20": pf(frame["net20"]),
                "total_net20_units": daily_total,
                "cumulative_net20_units": cumulative,
                "drawdown_net20_units": cumulative - peak,
            }
        )

    symbol_rows = []
    for symbol, frame in work.groupby("symbol", sort=True):
        symbol_rows.append(
            {
                "symbol": symbol,
                "n": int(len(frame)),
                "mean_gross_bp": float(frame["gross_ret"].mean() * 1e4),
                "mean_net10_bp": float(frame["net10"].mean() * 1e4),
                "mean_net20_bp": float(frame["net20"].mean() * 1e4),
                "win_rate_net20": float((frame["net20"] > 0).mean()),
                "profit_factor_net20": pf(frame["net20"]),
                "total_net20_units": float(frame["net20"].sum()),
            }
        )
    return pd.DataFrame(daily_rows), pd.DataFrame(symbol_rows)


def scope_rows(summary: dict) -> list[dict]:
    scopes = [
        (1, "未过滤短向候选池", summary["unfiltered_pool"]),
        (2, "B2 edge3 去重", summary["selected_primary_edge3_dedup"]),
        (3, "B2 edge2 敏感性", summary["selected_sensitivity_edge2_dedup"]),
        (4, "B2 置信度最高10%（诊断）", summary["detector_confidence_top_decile"]),
    ]
    return [
        {
            "scope_order": order,
            "scope": label,
            "n": data["n"],
            "symbols": data["symbols"],
            "gross_bp": data["mean_gross_bp"],
            "net10_bp": data["mean_net_taker_10bp"],
            "net20_bp": data["mean_net_conservative_20bp"],
            "win20": data["win_rate_net_conservative"],
            "pf10": data["profit_factor_net_taker"],
            "pf20": data["profit_factor_net_conservative"],
            "max_drawdown20_units": data["unit_sum_max_drawdown_conservative"],
        }
        for order, label, data in scopes
    ]


def artifact_source(generated_at: str) -> dict:
    return {
        "id": "replay_source",
        "label": "B2 pre-holdout short-L2 candidate-pool replay",
        "path": "analysis/output/p1_b2_short_l2_backtest_20260811_selected.csv",
        "query": {
            "engine": "duckdb",
            "language": "sql",
            "executed_at": generated_at,
            "description": "Loads causal B2-selected rows and recomputes fixed-cost economic metrics.",
            "tables_used": [
                "analysis/output/p1_b2_short_l2_backtest_20260811_selected.csv",
                "analysis/output/p1_b2_short_l2_backtest_20260811.json",
            ],
            "filters": [
                "short side only from the frozen L2 pre-holdout candidate pool",
                "2026-03-20T06:00:00Z <= signal_time < 2026-05-04T00:00:00Z",
                "outcome interval ends before 2026-05-04T00:00:00Z",
                "same-symbol +/-72 bars around every B2 validation endpoint excluded",
                "B2 fixed window=30, conf=0.35, primary tip edge=3 bars",
                "same-symbol causal dedup min gap=18 bars",
            ],
            "metric_definitions": [
                "net10 = frozen gross return - 0.001 swap taker round-trip cost",
                "net20 = frozen gross return - 0.002 conservative report round-trip cost",
                "profit factor = sum positive net returns / absolute sum negative net returns",
                "unit-sum max drawdown is drawdown of time-ordered unit-return cumulative sum, not portfolio equity",
                "confidence top decile uses the 90th percentile of B2 confidence as an exploratory diagnostic, not a frozen trade gate",
            ],
            "sql": (
                "SELECT *, gross_ret - 0.002 AS net20 "
                "FROM read_csv_auto('analysis/output/"
                "p1_b2_short_l2_backtest_20260811_selected.csv', header=true) "
                "ORDER BY signal_time, candidate_id;"
            ),
        },
    }


def matched_source(generated_at: str) -> dict:
    return {
        "id": "matched_source",
        "label": "Same-symbol/month/ATR-quintile matched controls",
        "path": "analysis/output/p1_b2_short_l2_backtest_20260811_matched.csv",
        "query": {
            "engine": "duckdb",
            "language": "sql",
            "executed_at": generated_at,
            "description": "Loads frozen-pool matched controls and computes selected-minus-control excess.",
            "tables_used": [
                "analysis/output/p1_b2_short_l2_backtest_20260811_matched.csv"
            ],
            "filters": [
                "up to 8 controls per selected row",
                "same symbol, UTC month, and ATR quintile when at least 3 controls exist",
                "fallback to same symbol and UTC month",
                "same candidate_id and event_group_id excluded",
            ],
            "metric_definitions": [
                "excess = selected net20 - mean matched-control net20",
                "p-value = exact two-sided sign-flip over 7 UTC-week blocks",
            ],
            "sql": (
                "SELECT * FROM read_csv_auto('analysis/output/"
                "p1_b2_short_l2_backtest_20260811_matched.csv', header=true) "
                "ORDER BY signal_time, candidate_id;"
            ),
        },
    }


def build_artifact(summary: dict, daily: pd.DataFrame) -> dict:
    generated_at = summary["generated_at"]
    primary = summary["selected_primary_edge3_dedup"]
    pool = summary["unfiltered_pool"]
    matched = summary["matched_control"]
    top = summary["detector_confidence_top_decile"]
    top_match = summary["matched_control_top_decile"]
    scopes = scope_rows(summary)
    economics = [
        {**row, "cost_scenario": "10bp", "net_bp": row["net10_bp"]}
        for row in scopes[:3]
    ] + [
        {**row, "cost_scenario": "20bp", "net_bp": row["net20_bp"]}
        for row in scopes[:3]
    ]
    monthly = [
        {
            "month": row["month"],
            "n": row["n"],
            "gross_bp": row["mean_gross_bp"],
            "net10_bp": row["mean_net_taker_10bp"],
            "net20_bp": row["mean_net_conservative_20bp"],
            "win20": row["win_rate_net_conservative"],
            "pf20": row["profit_factor_net_conservative"],
        }
        for row in summary["monthly"]
    ]
    confidence = [
        {
            "quartile": row["quartile"],
            "conf_min": row["conf_min"],
            "conf_max": row["conf_max"],
            "n": row["n"],
            "gross_bp": row["mean_gross_bp"],
            "net10_bp": row["mean_net_taker_10bp"],
            "net20_bp": row["mean_net_conservative_20bp"],
            "win20": row["win_rate_net_conservative"],
            "pf20": row["profit_factor_net_conservative"],
        }
        for row in summary["confidence_quartiles"]
    ]
    matched_rows = [
        {
            "scope": "B2 全部 edge3",
            "selected_n": primary["n"],
            "matched_n": matched["n_matched"],
            "coverage": matched["n_matched"] / primary["n"],
            "selected_net20_bp": matched["mean_selected_net_20bp"] * 1e4,
            "control_net20_bp": matched["mean_control_net_20bp"] * 1e4,
            "excess_bp": matched["mean_excess_bp"],
            "p_value": matched["exact_week_signflip_p"],
            "week_blocks": matched["n_utc_week_blocks"],
        },
        {
            "scope": "B2 置信度最高10%（诊断）",
            "selected_n": top["n"],
            "matched_n": top_match["n_matched"],
            "coverage": top_match["n_matched"] / top["n"],
            "selected_net20_bp": top_match["mean_selected_net_20bp"] * 1e4,
            "control_net20_bp": top_match["mean_control_net_20bp"] * 1e4,
            "excess_bp": top_match["mean_excess_bp"],
            "p_value": top_match["exact_week_signflip_p"],
            "week_blocks": top_match["n_utc_week_blocks"],
        },
    ]
    outcomes = [
        {
            "exit_reason": reason,
            "n": count,
            "share": count / primary["n"],
        }
        for reason, count in primary["outcomes"].items()
    ]
    protocol = [
        {"item": "回放范围", "value": "冻结 short-L2 候选池；不是全市场扫描"},
        {"item": "输入窗口", "value": "mapped_signal_i 及之前 30 根 K 线"},
        {"item": "B2 门", "value": "conf=0.35；tip/tip-1/tip-2（edge3）"},
        {"item": "去重", "value": "同币因果最小间隔 18 bars"},
        {"item": "验证隔离", "value": "排除同币所有 B2 val 端点 +/-72 bars"},
        {"item": "方向", "value": "short，由冻结 L2 pool 提供；YOLO 本身不判方向"},
        {"item": "入场", "value": "下一根 open"},
        {"item": "出场", "value": "TP5 ATR / SL2 ATR / 最长72 bars；同 bar 保守判 SL"},
        {"item": "成本", "value": "10bp swap taker；20bp 保守报告敏感性"},
        {"item": "holdout", "value": "未读取；max outcome end=2026-05-03 22:45 UTC"},
    ]
    replay_source = artifact_source(generated_at)
    control_source = matched_source(generated_at)

    cards = [
        {
            "id": "selected_n",
            "description": "B2 edge3 开火并按同币 18 bars 去重后的短向候选数。",
            "dataset": "headline",
            "sourceId": "replay_source",
            "metrics": [{"label": "B2 去重信号", "field": "selected_n", "format": "number"}],
        },
        {
            "id": "net10",
            "description": "冻结 gross return 扣 10bp swap taker 往返成本后的逐笔均值。",
            "dataset": "headline",
            "sourceId": "replay_source",
            "metrics": [
                {"label": "10bp 后均值", "field": "net10_bp", "format": "number", "unit": " bp", "signed": True},
                {"label": "较未过滤池", "field": "net10_vs_pool_bp", "format": "number", "unit": " bp", "signed": True},
            ],
        },
        {
            "id": "pf10",
            "description": "10bp 成本下，正收益总和除以负收益绝对总和。",
            "dataset": "headline",
            "sourceId": "replay_source",
            "metrics": [{"label": "10bp 后 PF", "field": "pf10", "format": "number"}],
        },
        {
            "id": "matched_excess",
            "description": "同币×同月×ATR 桶匹配随机对照的平均超额；p 为 7 周块精确双侧符号翻转。",
            "dataset": "headline",
            "sourceId": "matched_source",
            "metrics": [
                {"label": "匹配超额", "field": "matched_excess_bp", "format": "number", "unit": " bp", "signed": True},
                {"label": "周块 p", "field": "matched_p", "format": "number"},
            ],
        },
    ]
    charts = [
        {
            "id": "cost_chart",
            "title": "未过滤池与 B2 筛选后的平均净收益",
            "subtitle": "edge2 与 edge3 在本轮完全相同；10bp 与20bp均为固定成本口径。",
            "type": "bar",
            "dataset": "economics",
            "sourceId": "replay_source",
            "valueFormat": "number",
            "unit": "bp",
            "encodings": {
                "x": {"field": "scope", "type": "nominal", "label": "范围"},
                "y": {"field": "net_bp", "type": "quantitative", "label": "平均净收益", "unit": "bp"},
                "color": {"field": "cost_scenario", "type": "nominal", "label": "成本"},
                "tooltip": [
                    {"field": "n", "type": "quantitative", "label": "样本"},
                    {"field": "gross_bp", "type": "quantitative", "label": "毛收益", "unit": "bp"},
                    {"field": "pf20", "type": "quantitative", "label": "20bp PF"},
                ],
            },
            "palette": {"kind": "categorical", "name": "category10"},
        },
        {
            "id": "monthly_chart",
            "title": "B2 edge3 的月度平均净收益（20bp）",
            "subtitle": "2026-05 只含 5 月 1–3 日，不能与完整月份等权比较。",
            "type": "bar",
            "dataset": "monthly",
            "sourceId": "replay_source",
            "valueFormat": "number",
            "unit": "bp",
            "encodings": {
                "x": {"field": "month", "type": "ordinal", "label": "UTC 月"},
                "y": {"field": "net20_bp", "type": "quantitative", "label": "平均净收益", "unit": "bp"},
                "tooltip": [
                    {"field": "n", "type": "quantitative", "label": "信号"},
                    {"field": "pf20", "type": "quantitative", "label": "PF"},
                    {"field": "win20", "type": "quantitative", "label": "净胜率", "format": "percent"},
                ],
            },
        },
        {
            "id": "confidence_chart",
            "title": "B2 置信度四分位的平均净收益（20bp）",
            "subtitle": "仅作排序诊断；结果非单调，禁止据此事后调 conf。",
            "type": "bar",
            "dataset": "confidence",
            "sourceId": "replay_source",
            "valueFormat": "number",
            "unit": "bp",
            "encodings": {
                "x": {"field": "quartile", "type": "ordinal", "label": "B2 confidence 四分位"},
                "y": {"field": "net20_bp", "type": "quantitative", "label": "平均净收益", "unit": "bp"},
                "tooltip": [
                    {"field": "conf_min", "type": "quantitative", "label": "最低 conf"},
                    {"field": "conf_max", "type": "quantitative", "label": "最高 conf"},
                    {"field": "n", "type": "quantitative", "label": "样本"},
                    {"field": "pf20", "type": "quantitative", "label": "PF"},
                ],
            },
        },
    ]
    tables = [
        {
            "id": "scope_table",
            "title": "回放口径明细",
            "subtitle": "最高10%是探索性诊断，不是冻结交易门。",
            "dataset": "scope",
            "sourceId": "replay_source",
            "defaultSort": {"field": "scope_order", "direction": "asc"},
            "layout": "full",
            "columns": [
                {"field": "scope_order", "label": "#", "format": "number"},
                {"field": "scope", "label": "范围", "type": "text"},
                {"field": "n", "label": "笔数", "format": "number"},
                {"field": "symbols", "label": "币种", "format": "number"},
                {"field": "gross_bp", "label": "毛均值(bp)", "format": "number", "movement": True},
                {"field": "net10_bp", "label": "10bp净均值", "format": "number", "movement": True},
                {"field": "pf10", "label": "10bp PF", "format": "number"},
                {"field": "net20_bp", "label": "20bp净均值", "format": "number", "movement": True},
                {"field": "pf20", "label": "20bp PF", "format": "number"},
                {"field": "win20", "label": "20bp胜率", "format": "percent"},
                {"field": "max_drawdown20_units", "label": "单位和最大回撤", "format": "number", "movement": True},
            ],
        },
        {
            "id": "matched_table",
            "title": "匹配随机对照",
            "subtitle": "p<0.01 才满足项目确认门；本轮两个范围均未达到。",
            "dataset": "matched",
            "sourceId": "matched_source",
            "defaultSort": {"field": "selected_n", "direction": "desc"},
            "layout": "full",
            "columns": [
                {"field": "scope", "label": "范围", "type": "text"},
                {"field": "selected_n", "label": "信号", "format": "number"},
                {"field": "matched_n", "label": "已匹配", "format": "number"},
                {"field": "coverage", "label": "覆盖率", "format": "percent"},
                {"field": "selected_net20_bp", "label": "模型20bp净均值", "format": "number", "movement": True},
                {"field": "control_net20_bp", "label": "随机20bp净均值", "format": "number", "movement": True},
                {"field": "excess_bp", "label": "超额(bp)", "format": "number", "movement": True},
                {"field": "p_value", "label": "周块p", "format": "number"},
                {"field": "week_blocks", "label": "周块", "format": "number"},
            ],
        },
        {
            "id": "monthly_table",
            "title": "月度拆分",
            "subtitle": "所有月份 20bp 后均为负。",
            "dataset": "monthly",
            "sourceId": "replay_source",
            "defaultSort": {"field": "month", "direction": "asc"},
            "columns": [
                {"field": "month", "label": "UTC月", "type": "text"},
                {"field": "n", "label": "笔数", "format": "number"},
                {"field": "gross_bp", "label": "毛均值(bp)", "format": "number", "movement": True},
                {"field": "net10_bp", "label": "10bp净均值", "format": "number", "movement": True},
                {"field": "net20_bp", "label": "20bp净均值", "format": "number", "movement": True},
                {"field": "pf20", "label": "20bp PF", "format": "number"},
                {"field": "win20", "label": "20bp胜率", "format": "percent"},
            ],
        },
        {
            "id": "confidence_table",
            "title": "置信度分层",
            "subtitle": "Q4 接近盈亏平衡但四分位不单调；最高10%另见回放明细与匹配表。",
            "dataset": "confidence",
            "sourceId": "replay_source",
            "defaultSort": {"field": "quartile", "direction": "asc"},
            "columns": [
                {"field": "quartile", "label": "分位", "type": "text"},
                {"field": "conf_min", "label": "conf min", "format": "number"},
                {"field": "conf_max", "label": "conf max", "format": "number"},
                {"field": "n", "label": "笔数", "format": "number"},
                {"field": "gross_bp", "label": "毛均值(bp)", "format": "number", "movement": True},
                {"field": "net10_bp", "label": "10bp净均值", "format": "number", "movement": True},
                {"field": "net20_bp", "label": "20bp净均值", "format": "number", "movement": True},
                {"field": "pf20", "label": "20bp PF", "format": "number"},
            ],
        },
        {
            "id": "outcome_table",
            "title": "出场原因",
            "subtitle": "冻结 TP5/SL2/72-bar 标签，不重新定义障碍。",
            "dataset": "outcomes",
            "sourceId": "replay_source",
            "defaultSort": {"field": "n", "direction": "desc"},
            "columns": [
                {"field": "exit_reason", "label": "原因", "type": "text"},
                {"field": "n", "label": "笔数", "format": "number"},
                {"field": "share", "label": "占比", "format": "percent"},
            ],
        },
        {
            "id": "protocol_table",
            "title": "协议与安全审计",
            "subtitle": "用于界定这份回放能回答什么、不能回答什么。",
            "dataset": "protocol",
            "sourceId": "replay_source",
            "defaultSort": {"field": "item", "direction": "asc"},
            "columns": [
                {"field": "item", "label": "项目", "type": "text"},
                {"field": "value", "label": "冻结口径", "type": "text"},
            ],
        },
    ]
    blocks = [
        {"id": "title", "type": "markdown", "body": "# Local Signal V2 B2：pre-holdout 短向候选池经济回放"},
        {
            "id": "executive",
            "type": "markdown",
            "sourceId": "replay_source",
            "body": (
                "## Executive Summary\n\n"
                f"- **B2 单独不能作为交易策略。** 去重后 {primary['n']:,} 笔，10bp 后均值 **{primary['mean_net_taker_10bp']:.2f}bp**、PF **{primary['profit_factor_net_taker']:.3f}**；20bp 后 **{primary['mean_net_conservative_20bp']:.2f}bp**、PF **{primary['profit_factor_net_conservative']:.3f}**。\n"
                f"- **没有改善未过滤池。** 10bp 后 B2 比未过滤短向候选池低 **{primary['mean_net_taker_10bp'] - pool['mean_net_taker_10bp']:.2f}bp/笔**。\n"
                f"- **匹配超额未获统计支持。** 同币×同月×ATR 桶对照为 **+{matched['mean_excess_bp']:.2f}bp**，但 7 周块精确 p=**{matched['exact_week_signflip_p']:.3f}**。\n"
                f"- **最高置信度 10% 仅是线索。** 388 笔在 20bp 后 **+{top['mean_net_conservative_20bp']:.2f}bp**、PF **{top['profit_factor_net_conservative']:.3f}**，但匹配检验 p=**{top_match['exact_week_signflip_p']:.3f}**，不能据此调 conf 或宣称盈利。\n"
                "- **项目含义：** B2 可继续扮演 L1 候选生成器，但经济选择必须由独立、时间切分的 P2 判断层证明；B2 本身不得部署交易。"
            ),
        },
        {"id": "metrics", "type": "metric-strip", "cardIds": ["selected_n", "net10", "pf10", "matched_excess"]},
        {
            "id": "economics_finding",
            "type": "markdown",
            "sourceId": "replay_source",
            "body": "## 整体经济结果为负\n\nB2 的原始开火率为 49.78%，而冻结候选池本身已满足 18-bar 间隔，因此 3,880 个原始开火全部保留。edge2 与 edge3 输出完全相同。固定 10bp 和 20bp 两种成本下，B2 都没有超过未过滤池。",
        },
        {"id": "cost_chart_block", "type": "chart", "chartId": "cost_chart"},
        {"id": "scope_table_block", "type": "table", "tableId": "scope_table", "layout": "full"},
        {
            "id": "control_finding",
            "type": "markdown",
            "sourceId": "matched_source",
            "body": "## 匹配随机对照不支持稳定超额\n\n全部 B2 信号的对照覆盖率为 94.48%。平均超额虽然为正，但周块 p=0.891，说明符号与幅度跨周不稳定。最高置信度 10% 的匹配超额更高，但 p=0.453，仍不满足 p<0.01 的确认门。",
        },
        {"id": "matched_table_block", "type": "table", "tableId": "matched_table", "layout": "full"},
        {
            "id": "time_finding",
            "type": "markdown",
            "sourceId": "replay_source",
            "body": "## 负收益不是单月口径造成\n\n3 月、4 月及 5 月 1–3 日在 20bp 后均为负。5 月仅 255 笔且不是完整月份，只能作为尾段压力信号。单位和最大回撤用于同口径比较，不代表真实资金曲线或可同时执行的组合。",
        },
        {"id": "monthly_chart_block", "type": "chart", "chartId": "monthly_chart"},
        {"id": "monthly_table_block", "type": "table", "tableId": "monthly_table"},
        {
            "id": "confidence_finding",
            "type": "markdown",
            "sourceId": "replay_source",
            "body": "## 置信度含有弱排序线索，但不允许事后调门\n\n四分位结果并不单调：Q2 好于 Q1、Q3最差、Q4最好。最高10%在本池为正，但阈值 0.4488 是看到经济标签后的分层诊断，不是预注册交易阈值；后续若利用，只能作为 P2 输入或在新的独立时间块上冻结验证。",
        },
        {"id": "confidence_chart_block", "type": "chart", "chartId": "confidence_chart"},
        {"id": "confidence_table_block", "type": "table", "tableId": "confidence_table"},
        {"id": "outcome_table_block", "type": "table", "tableId": "outcome_table"},
        {
            "id": "methodology",
            "type": "markdown",
            "sourceId": "replay_source",
            "body": "## 方法、基线与复现范围\n\n主基线是同一冻结短向候选池未经过 B2 筛选的结果；方向性归因使用同币×同月×ATR 五分位随机对照。B2 是固定阈值检测器，不产生 LightGBM AUC；本轮 val AUC 与单特征基线均不适用。项目要求的 top-decile 指标在这里按 detector confidence 诊断，不能替代未来 P2 排序分数。",
        },
        {"id": "protocol_table_block", "type": "table", "tableId": "protocol_table", "layout": "full"},
        {
            "id": "files",
            "type": "markdown",
            "body": "## 可核对的数据文件\n\n- 全部 7,795 个已推理候选：`analysis/output/p1_b2_short_l2_backtest_20260811_rows.csv`\n- B2 edge3 去重后的 3,880 笔：`analysis/output/p1_b2_short_l2_backtest_20260811_selected.csv`\n- 3,666 笔匹配随机对照：`analysis/output/p1_b2_short_l2_backtest_20260811_matched.csv`\n- 日度与币种汇总：`analysis/output/p1_b2_short_l2_backtest_report_20260811/daily.csv`、`symbol.csv`",
        },
        {
            "id": "caveats",
            "type": "markdown",
            "sourceId": "replay_source",
            "body": "## 风险与诚实声明\n\n- 这是 B2 × 冻结 short-L2 候选池回放，不是全市场逐 bar 扫描，也不是 B2+LightGBM 端到端回测。\n- B2 权重与 conf=0.35 在 P1 验证集上选择；本轮额外排除同币全部 val 端点前后 72 bars，去掉直接事件/结果窗重叠，但剩余区间仍属于开发期而非最终确认集。\n- 置信度最高10%与四分位是事后诊断，禁止把 0.4488 自动写回阈值。\n- 冻结收益标签之间可能有时间重叠；周块符号翻转比逐笔独立检验更保守，但只有 7 个周块。\n- 未读取 holdout，未修改成本/障碍/新鲜度门，未 promote、未部署、未下单。",
        },
        {
            "id": "next_steps",
            "type": "markdown",
            "body": "## 建议下一步\n\n1. 保留 B2 作为 L1 候选生成器，不将其单独晋升为交易策略。\n2. P2 判断层按时间切分训练，只使用信号 bar 及之前的特征，把 B2 confidence 作为一个候选特征而非直接阈值。\n3. P2 的冻结裁决仍以 top-decile 20bp 后净收益>0、匹配随机对照和 p<0.01 为门；不读取 holdout。\n4. 如果 owner 要把最高10%作为独立策略假设，必须先冻结阈值与新时间块，再验证，不能复用本回放作为确认。",
        },
    ]
    headline = [
        {
            "selected_n": primary["n"],
            "net10_bp": primary["mean_net_taker_10bp"],
            "net10_vs_pool_bp": primary["mean_net_taker_10bp"] - pool["mean_net_taker_10bp"],
            "pf10": primary["profit_factor_net_taker"],
            "matched_excess_bp": matched["mean_excess_bp"],
            "matched_p": matched["exact_week_signflip_p"],
        }
    ]
    snapshot = {
        "version": 1,
        "generatedAt": generated_at,
        "status": "ready",
        "datasets": {
            "headline": headline,
            "scope": scopes,
            "economics": economics,
            "matched": matched_rows,
            "monthly": monthly,
            "confidence": confidence,
            "outcomes": outcomes,
            "protocol": protocol,
            "daily": daily.to_dict(orient="records"),
        },
    }
    manifest = {
        "version": 1,
        "surface": "report",
        "title": "Local Signal V2 B2：pre-holdout 短向候选池经济回放",
        "description": "最新 B2 权重在冻结 short-L2 候选池上的因果经济回放；不读取 holdout。",
        "generatedAt": generated_at,
        "cards": cards,
        "charts": charts,
        "tables": tables,
        "sources": [replay_source, control_source],
        "blocks": blocks,
    }
    return {
        "surface": "report",
        "manifest": manifest,
        "snapshot": snapshot,
        "sources": [replay_source, control_source],
    }


def build_markdown(summary: dict) -> str:
    primary = summary["selected_primary_edge3_dedup"]
    pool = summary["unfiltered_pool"]
    matched = summary["matched_control"]
    top = summary["detector_confidence_top_decile"]
    top_match = summary["matched_control_top_decile"]
    monthly = summary["monthly"]
    conf = summary["confidence_quartiles"]
    return f"""# Local Signal V2 B2：pre-holdout 短向候选池经济回放

生成时间：{summary['generated_at']}
结论等级：开发期经济可行性诊断，不是最终确认，不是生产回测。

## Executive Summary

- **B2 单独不能作为交易策略。** 去重后 {primary['n']:,} 笔，10bp 后均值 {primary['mean_net_taker_10bp']:.2f}bp、PF {primary['profit_factor_net_taker']:.3f}；20bp 后 {primary['mean_net_conservative_20bp']:.2f}bp、PF {primary['profit_factor_net_conservative']:.3f}。
- **没有改善未过滤池。** 10bp 后 B2 比未过滤短向候选池低 {primary['mean_net_taker_10bp'] - pool['mean_net_taker_10bp']:.2f}bp/笔。
- **匹配超额未获统计支持。** 同币×同月×ATR 桶对照超额 +{matched['mean_excess_bp']:.2f}bp，7 周块精确双侧 p={matched['exact_week_signflip_p']:.3f}。
- **最高置信度 10% 是线索，不是门。** 388 笔在 20bp 后 +{top['mean_net_conservative_20bp']:.2f}bp、PF {top['profit_factor_net_conservative']:.3f}，但匹配 p={top_match['exact_week_signflip_p']:.3f}，不满足 p<0.01。
- **项目方向不变。** B2 只做 L1 候选生成；是否可交易必须由独立时间切分的 P2 判断层证明。

## 数据与协议

| 项目 | 值 |
|---|---:|
| 原始 pre-holdout short-L2 行数 | {summary['source_rows_in_time_range']:,} |
| 排除 B2 val 端点 ±72 bars | {summary['validation_overlap_excluded']:,} |
| 最终可回放候选 | {summary['eligible_rows']:,} |
| 币种 | {summary['symbols']} |
| 信号时间 | {summary['time_range']['min_signal']} — {summary['time_range']['max_signal']} |
| 最晚 outcome end | {summary['time_range']['max_outcome_end']} |
| holdout 起点 | {summary['time_range']['holdout_start']} |
| holdout 读取 | 0 次 / False |
| B2 | fixed 30 bars, conf=0.35, edge3 |
| 方向 | short（冻结 L2 pool 提供，YOLO 不判方向） |
| 入场 / 出场 | next open / TP5 ATR、SL2 ATR、最长72 bars |
| 成本 | 10bp swap taker；20bp 保守报告敏感性 |

## 主要结果

| 范围 | n | 毛均值bp | 10bp净均值 | 10bp PF | 20bp净均值 | 20bp PF | 20bp胜率 | 单位和最大回撤(20bp) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 未过滤池 | {pool['n']:,} | {pool['mean_gross_bp']:.2f} | {pool['mean_net_taker_10bp']:.2f} | {pool['profit_factor_net_taker']:.3f} | {pool['mean_net_conservative_20bp']:.2f} | {pool['profit_factor_net_conservative']:.3f} | {pool['win_rate_net_conservative']:.2%} | {pool['unit_sum_max_drawdown_conservative']:.3f} |
| B2 edge3 去重 | {primary['n']:,} | {primary['mean_gross_bp']:.2f} | {primary['mean_net_taker_10bp']:.2f} | {primary['profit_factor_net_taker']:.3f} | {primary['mean_net_conservative_20bp']:.2f} | {primary['profit_factor_net_conservative']:.3f} | {primary['win_rate_net_conservative']:.2%} | {primary['unit_sum_max_drawdown_conservative']:.3f} |
| B2 edge2 敏感性 | {summary['selected_sensitivity_edge2_dedup']['n']:,} | {summary['selected_sensitivity_edge2_dedup']['mean_gross_bp']:.2f} | {summary['selected_sensitivity_edge2_dedup']['mean_net_taker_10bp']:.2f} | {summary['selected_sensitivity_edge2_dedup']['profit_factor_net_taker']:.3f} | {summary['selected_sensitivity_edge2_dedup']['mean_net_conservative_20bp']:.2f} | {summary['selected_sensitivity_edge2_dedup']['profit_factor_net_conservative']:.3f} | {summary['selected_sensitivity_edge2_dedup']['win_rate_net_conservative']:.2%} | {summary['selected_sensitivity_edge2_dedup']['unit_sum_max_drawdown_conservative']:.3f} |
| conf 最高10%（诊断） | {top['n']:,} | {top['mean_gross_bp']:.2f} | {top['mean_net_taker_10bp']:.2f} | {top['profit_factor_net_taker']:.3f} | {top['mean_net_conservative_20bp']:.2f} | {top['profit_factor_net_conservative']:.3f} | {top['win_rate_net_conservative']:.2%} | {top['unit_sum_max_drawdown_conservative']:.3f} |

> 最大回撤是按时间排序的“每笔单位收益累加”回撤，不是仓位化资金曲线。候选结果可能重叠，不能解释为可同时执行组合。

## 匹配随机对照

| 范围 | 信号 | 已匹配 | 覆盖率 | 模型20bp净均值 | 随机20bp净均值 | 超额bp | 周块p | 周块 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| B2 全部 edge3 | {primary['n']:,} | {matched['n_matched']:,} | {matched['n_matched']/primary['n']:.2%} | {matched['mean_selected_net_20bp']*1e4:.2f} | {matched['mean_control_net_20bp']*1e4:.2f} | {matched['mean_excess_bp']:.2f} | {matched['exact_week_signflip_p']:.3f} | {matched['n_utc_week_blocks']} |
| conf 最高10%（诊断） | {top['n']:,} | {top_match['n_matched']:,} | {top_match['n_matched']/top['n']:.2%} | {top_match['mean_selected_net_20bp']*1e4:.2f} | {top_match['mean_control_net_20bp']*1e4:.2f} | {top_match['mean_excess_bp']:.2f} | {top_match['exact_week_signflip_p']:.3f} | {top_match['n_utc_week_blocks']} |

## 月度拆分

| UTC月 | n | 毛均值bp | 10bp净均值 | 20bp净均值 | 20bp PF | 20bp胜率 |
|---|---:|---:|---:|---:|---:|---:|
""" + "\n".join(
        f"| {r['month']} | {r['n']:,} | {r['mean_gross_bp']:.2f} | {r['mean_net_taker_10bp']:.2f} | {r['mean_net_conservative_20bp']:.2f} | {r['profit_factor_net_conservative']:.3f} | {r['win_rate_net_conservative']:.2%} |"
        for r in monthly
    ) + f"""

2026-05 只含 5 月 1–3 日，不与完整月份等权比较。所有月段在 20bp 后均为负。

## B2 confidence 分层

| 分位 | conf范围 | n | 毛均值bp | 10bp净均值 | 20bp净均值 | 20bp PF |
|---|---:|---:|---:|---:|---:|---:|
""" + "\n".join(
        f"| {r['quartile']} | {r['conf_min']:.4f}–{r['conf_max']:.4f} | {r['n']:,} | {r['mean_gross_bp']:.2f} | {r['mean_net_taker_10bp']:.2f} | {r['mean_net_conservative_20bp']:.2f} | {r['profit_factor_net_conservative']:.3f} |"
        for r in conf
    ) + f"""

四分位不单调。最高10%阈值 {top['cutoff']:.4f} 是看到本回放结果后的诊断切点，不是可写回生产的阈值。

## 项目必报指标对照

| 指标 | 本轮结果 |
|---|---|
| val AUC | N/A：B2 是固定阈值 YOLO 检测器，本回放没有 LightGBM 排序分数 |
| top-decile 毛 / 10bp净 / 20bp净 | {top['mean_gross_bp']:.2f} / {top['mean_net_taker_10bp']:.2f} / {top['mean_net_conservative_20bp']:.2f} bp（仅 detector confidence 诊断） |
| top-decile 20bp胜率 / PF | {top['win_rate_net_conservative']:.2%} / {top['profit_factor_net_conservative']:.3f} |
| top-decile 匹配随机超额 / p | +{top_match['mean_excess_bp']:.2f}bp / {top_match['exact_week_signflip_p']:.3f}，不满足 p<0.01 |
| 单特征基线 | N/A：P2 尚未训练；主基线为未过滤冻结短向候选池 |

## 结果解读

1. B2 在全部固定阈值开火上没有经济选择力：比未过滤池还低 {primary['mean_net_taker_10bp'] - pool['mean_net_taker_10bp']:.2f}bp/笔。
2. Q4 与最高10%显示 B2 confidence 可能携带排序信息，但四分位不单调、跨周不稳定，证据不足以单独交易。
3. 正确方向是让 P2 在独立时间切分上判断 B2 候选，而不是围绕本回放继续调 YOLO conf。

## 数据文件

- `analysis/output/p1_b2_short_l2_backtest_20260811.json`：完整汇总与协议。
- `analysis/output/p1_b2_short_l2_backtest_20260811_rows.csv`：7,795 个逐候选 B2 预测。
- `analysis/output/p1_b2_short_l2_backtest_20260811_selected.csv`：3,880 笔主结果。
- `analysis/output/p1_b2_short_l2_backtest_20260811_matched.csv`：3,666 笔匹配对照。
- `analysis/output/p1_b2_short_l2_backtest_report_20260811/daily.csv`：日度汇总。
- `analysis/output/p1_b2_short_l2_backtest_report_20260811/symbol.csv`：币种汇总。

## 完整复现命令

```bash
cd /Users/zhangzc/fable-trading
PYTHONPYCACHEPREFIX=/tmp/fable_pycache PYTHONPATH=.:../yoyo-trading \\
  .venv/bin/pytest -q tests/test_backtest_local_signal_v2_b2_short_pool.py

MPLCONFIGDIR=/tmp/mplconfig PYTHONPYCACHEPREFIX=/tmp/fable_pycache \\
  PYTHONPATH=.:../yoyo-trading .venv/bin/python -u \\
  scripts/backtest_local_signal_v2_b2_short_pool.py --device mps --batch 12

PYTHONPATH=.:../yoyo-trading .venv/bin/python \\
  scripts/build_p1_b2_short_l2_backtest_report.py

python3 scripts/md_to_html.py \\
  analysis/p1_b2_short_l2_backtest_20260811.md --out-dir analysis/html
```

## 风险与诚实声明

- 本轮是 B2 × 冻结 short-L2 候选池回放，不是全市场逐 bar 扫描，也不是 B2+LightGBM 端到端回测。
- B2 权重和 conf=0.35 来自 P1 开发期选择；已额外排除同币所有 val 端点前后72 bars，但剩余数据仍不是最终确认集。
- 置信度四分位和最高10%是事后诊断，禁止自动修改阈值。
- outcome 可能时间重叠；周块检验只有7块，统计功效有限。
- 未读取 holdout，未修改成本/障碍/新鲜度门，未 promote、未部署、未下单。

## 下一步选项

1. **建议：进入 P2 开发，但不晋升 B2 为交易策略。** P2 必须严格时间切分，并把 B2 confidence 仅作为一个候选特征。
2. 若要单独验证“最高10%”假设，需 owner 先冻结阈值与新时间块；不能复用本回放作确认。
3. 不建议继续围绕 B2 conf 调参；当前证据最需要的是判断层选择力，而不是再做检测层经济拟合。
"""


def main() -> int:
    summary = json.loads(SUMMARY_PATH.read_text())
    rows = pd.read_csv(ROWS_PATH, parse_dates=["signal_time", "interval_end"])
    selected = pd.read_csv(SELECTED_PATH, parse_dates=["signal_time", "interval_end"])
    matched = pd.read_csv(MATCHED_PATH)
    if len(rows) != summary["eligible_rows"]:
        raise ValueError("row count does not match summary")
    if len(selected) != summary["selected_primary_edge3_dedup"]["n"]:
        raise ValueError("selected count does not match summary")
    if len(matched) != summary["matched_control"]["n_matched"]:
        raise ValueError("matched count does not match summary")
    if rows["interval_end"].max() >= pd.Timestamp(summary["time_range"]["holdout_start"]):
        raise ValueError("refusing report whose outcomes touch holdout")

    daily, symbol = make_rollups(selected)
    artifact = build_artifact(summary, daily)
    report_md = build_markdown(summary)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    daily.to_csv(DAILY_PATH, index=False)
    symbol.to_csv(SYMBOL_PATH, index=False)
    ARTIFACT_PATH.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )
    MD_PATH.write_text(report_md)
    print(f"wrote {MD_PATH.relative_to(PROJECT)}")
    print(f"wrote {ARTIFACT_PATH.relative_to(PROJECT)}")
    print(f"wrote {DAILY_PATH.relative_to(PROJECT)} ({len(daily)} rows)")
    print(f"wrote {SYMBOL_PATH.relative_to(PROJECT)} ({len(symbol)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
