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
DENSITY_PATH = PROJECT / "analysis/output/p1_b2_density_diagnostic_20260811.json"
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


def density_source(generated_at: str) -> dict:
    return {
        "id": "density_source",
        "label": "B2 endpoint and proposal-pool density audit",
        "path": "analysis/output/p1_b2_density_diagnostic_20260811.json",
        "query": {
            "engine": "duckdb",
            "language": "sql",
            "executed_at": generated_at,
            "description": "Reconciles balanced validation endpoints with the frozen v10 proposal-led pool and checks threshold sensitivity and implementation parity.",
            "tables_used": [
                "analysis/output/p1_local_signal_v2/B2_event_eval.json",
                "analysis/output/local_signal_v2_p1_eval/B2/manifest.jsonl",
                "analysis/output/p1_b2_short_l2_backtest_20260811_rows.csv",
                "analysis/output/p1_dataset_manifest_20260803.json",
            ],
            "filters": [
                "selected threshold remains conf=0.35",
                "candidate outcomes end before 2026-05-04T00:00:00Z",
                "no continuous market scan and no executable order simulation",
            ],
            "metric_definitions": [
                "easy-negative fire rate = easy-negative endpoints with any box / easy-negative endpoints",
                "proposal-pool fire rate = rows with a B2 edge3 fire / eligible v10 proposal rows",
                "fires/day = proposal-pool fires / full calendar span between first and last proposal",
                "threshold ladder is diagnostic only and does not change the selected threshold",
            ],
            "sql": (
                "SELECT selected_threshold_evidence.proposal_pool.fire_rate AS pool_fire_rate, "
                "selected_threshold_evidence.proposal_pool.fires_per_calendar_span_day AS fires_per_day, "
                "selected_threshold_evidence.validation.easy_negative_endpoint_fire_rate AS easy_negative_rate, "
                "threshold_sensitivity_diagnostic_only[1].validation_recall AS validation_recall "
                "FROM read_json_auto('analysis/output/p1_b2_density_diagnostic_20260811.json');"
            ),
        },
    }


def build_artifact(summary: dict, density: dict, daily: pd.DataFrame) -> dict:
    generated_at = summary["generated_at"]
    primary = summary["selected_primary_edge3_dedup"]
    pool = summary["unfiltered_pool"]
    matched = summary["matched_control"]
    top = summary["detector_confidence_top_decile"]
    top_match = summary["matched_control_top_decile"]
    density_selected = density["selected_threshold_evidence"]
    validation_density = density_selected["validation"]
    pool_density = density_selected["proposal_pool"]
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
    density_stages = [
        {"stage_order": 1, "stage": "v10 预筛 proposal rows", "n": density["grain_reconciliation"]["proposal_pool"]["rows"]},
        {"stage_order": 2, "stage": "B2 L1 fire rows", "n": density_selected["proposal_pool_fires"]},
        {"stage_order": 3, "stage": "唯一 outcome event groups", "n": density_selected["proposal_pool_unique_event_groups"]},
    ]
    threshold_density = [
        {
            "threshold": f"{row['threshold']:.2f}",
            "recall": row["validation_recall"],
            "easy_negative_fire_rate": row["validation_easy_negative_fire_rate"],
            "proposal_fires": row["proposal_pool_fires"],
            "fires_per_day": row["proposal_pool_fires_per_calendar_span_day"],
        }
        for row in density["threshold_sensitivity_diagnostic_only"]
    ]
    grain = [
        {"grain_order": 1, "grain": "P1 平衡 endpoint 尺", "count": "715", "meaning": "358 正例 + 357 easy negatives；抽样验证，不是连续市场"},
        {"grain_order": 2, "grain": "v10 预筛 proposal rows", "count": "7,795", "meaning": "230 币、已预筛且同币至少间隔18根"},
        {"grain_order": 3, "grain": "B2 L1 fire rows", "count": "3,880", "meaning": "YOLO候选命中，不是订单"},
        {"grain_order": 4, "grain": "连续市场 L1 fires", "count": "未测", "meaning": "本轮未逐币×逐盘口 endpoint 扫描"},
        {"grain_order": 5, "grain": "可执行订单", "count": "未计算", "meaning": "P2 密度未过，P3 判断与执行被阻断"},
    ]
    replay_source = artifact_source(generated_at)
    control_source = matched_source(generated_at)
    density_audit_source = density_source(density["generated_at"])

    cards = [
        {
            "id": "pool_fire_rate",
            "description": "conf=0.35 在已经预筛的 v10 proposal pool 中仍然产生 B2 box 的行占比。",
            "dataset": "headline",
            "sourceId": "density_source",
            "metrics": [{"label": "proposal pool 命中率", "field": "pool_fire_rate", "format": "percent"}],
        },
        {
            "id": "fires_per_day",
            "description": "3,880 个 L1 fire rows 除以 proposal ledger 的完整日历跨度；不是生产订单/日。",
            "dataset": "headline",
            "sourceId": "density_source",
            "metrics": [{"label": "proposal ledger L1 fires/日", "field": "fires_per_day", "format": "number"}],
        },
        {
            "id": "easy_negative_rate",
            "description": "P1 平衡验证尺中，357 个 easy-negative endpoints 里有任意 B2 box 的比例。",
            "dataset": "headline",
            "sourceId": "density_source",
            "metrics": [{"label": "easy-negative 命中率", "field": "easy_negative_rate", "format": "percent"}],
        },
        {
            "id": "validation_recall",
            "description": "原 P1 选择点的正例 endpoint 召回；提高 conf 虽能降密度，但会迅速破坏该召回。",
            "dataset": "headline",
            "sourceId": "density_source",
            "metrics": [{"label": "conf=0.35 正例召回", "field": "validation_recall", "format": "percent"}],
        },
    ]
    charts = [
        {
            "id": "density_stage_chart",
            "title": "预筛候选池中的 B2 命中数量",
            "subtitle": "3,880 是 L1 fire rows；去重为 outcome event group 只减少 4.25%，两者都不是订单。",
            "type": "bar",
            "dataset": "density_stages",
            "sourceId": "density_source",
            "valueFormat": "number",
            "encodings": {
                "x": {"field": "stage", "type": "nominal", "label": "数据粒度"},
                "y": {"field": "n", "type": "quantitative", "label": "数量"},
                "tooltip": [{"field": "n", "type": "quantitative", "label": "数量"}],
            },
        },
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
                    {"field": "n", "type": "quantitative", "label": "候选行"},
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
            "id": "grain_table",
            "title": "数量口径：L1 fire 不等于订单",
            "subtitle": "连续市场触发量与可执行订单数在本轮都没有被计算。",
            "dataset": "grain",
            "sourceId": "density_source",
            "defaultSort": {"field": "grain_order", "direction": "asc"},
            "layout": "full",
            "columns": [
                {"field": "grain_order", "label": "#", "format": "number"},
                {"field": "grain", "label": "口径", "type": "text"},
                {"field": "count", "label": "数量", "type": "text"},
                {"field": "meaning", "label": "含义", "type": "text"},
            ],
        },
        {
            "id": "threshold_table",
            "title": "阈值敏感性（只诊断，不改阈值）",
            "subtitle": "依靠抬 conf 压密度会同时摧毁正例召回，不能替代 hard-negative mining。",
            "dataset": "threshold_density",
            "sourceId": "density_source",
            "defaultSort": {"field": "threshold", "direction": "asc"},
            "layout": "full",
            "columns": [
                {"field": "threshold", "label": "conf", "type": "text"},
                {"field": "recall", "label": "正例召回", "format": "percent"},
                {"field": "easy_negative_fire_rate", "label": "easy-neg命中率", "format": "percent"},
                {"field": "proposal_fires", "label": "proposal fires", "format": "number"},
                {"field": "fires_per_day", "label": "fires/日", "format": "number"},
            ],
        },
        {
            "id": "scope_table",
            "title": "回放口径明细",
            "subtitle": "最高10%是探索性诊断，不是冻结候选门或订单门。",
            "dataset": "scope",
            "sourceId": "replay_source",
            "defaultSort": {"field": "scope_order", "direction": "asc"},
            "layout": "full",
            "columns": [
                {"field": "scope_order", "label": "#", "format": "number"},
                {"field": "scope", "label": "范围", "type": "text"},
                {"field": "n", "label": "候选行", "format": "number"},
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
                {"field": "selected_n", "label": "L1 fire rows", "format": "number"},
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
                {"field": "n", "label": "候选行", "format": "number"},
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
                {"field": "n", "label": "候选行", "format": "number"},
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
                {"field": "n", "label": "候选行", "format": "number"},
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
        {"id": "title", "type": "markdown", "body": "# Local Signal V2 B2：候选密度与收益诊断"},
        {
            "id": "executive",
            "type": "markdown",
            "body": (
                "## Executive Summary\n\n"
                "- **撤回上一版把 3,880 写成“交易/开单”的表述。** 它们是 B2 在 v10 预筛 proposal ledger 上的 L1 fire rows，不是订单；连续市场触发量和可执行订单数都未计算。\n"
                f"- **但模型密度确实不合格。** conf=0.35 命中 {pool_density['fires']:,}/{density['grain_reconciliation']['proposal_pool']['rows']:,} 个预筛候选（**{pool_density['fire_rate']:.2%}**），折合 proposal ledger **{pool_density['fires_per_calendar_span_day']:.2f} fires/日**；P1 easy negatives 也命中 {validation_density['easy_negative_endpoints_with_any_box']}/{validation_density['easy_negative_endpoints']}（**{validation_density['easy_negative_endpoint_fire_rate']:.2%}**）。\n"
                "- **不是重复计数或推理路径 bug。** candidate_id 唯一，同币最小间隔18根，edge2=edge3，数组/PNG 8 个样本的框数与数值完全一致。\n"
                "- **不能靠抬阈值修。** conf=0.45 虽降至 8.35 fires/日，但验证召回同时从73.46%降至6.98%。\n"
                "- **项目方向纠正：** B2 停在密度失败；下一步按交接规范进入 P2 hard-negative mining + 连续因果盘口密度回放。P3 判断层在 L1 密度可信前阻断。"
            ),
        },
        {"id": "metrics", "type": "metric-strip", "cardIds": ["pool_fire_rate", "fires_per_day", "easy_negative_rate", "validation_recall"]},
        {"id": "grain_table_block", "type": "table", "tableId": "grain_table", "layout": "full"},
        {"id": "density_stage_chart_block", "type": "chart", "chartId": "density_stage_chart"},
        {"id": "threshold_table_block", "type": "table", "tableId": "threshold_table", "layout": "full"},
        {
            "id": "economics_finding",
            "type": "markdown",
            "sourceId": "replay_source",
            "body": "## 反事实收益诊断同样为负\n\n以下经济表只是回答“如果把每个 L1 fire row 都强行视作 short 会怎样”，不是订单回测。固定10bp和20bp两种成本下，B2候选结果都没有超过未过滤proposal pool。",
        },
        {"id": "cost_chart_block", "type": "chart", "chartId": "cost_chart"},
        {"id": "scope_table_block", "type": "table", "tableId": "scope_table", "layout": "full"},
        {
            "id": "control_finding",
            "type": "markdown",
            "sourceId": "matched_source",
            "body": "## 匹配随机候选不支持稳定超额\n\n全部 B2 fire rows 的对照覆盖率为94.48%。平均超额虽然为正，但周块p=0.891，说明符号与幅度跨周不稳定。最高置信度10%的匹配超额更高，但p=0.453，仍不满足p<0.01的确认门。",
        },
        {"id": "matched_table_block", "type": "table", "tableId": "matched_table", "layout": "full"},
        {
            "id": "time_finding",
            "type": "markdown",
            "sourceId": "replay_source",
            "body": "## 负收益不是单月口径造成\n\n3月、4月及5月1–3日在20bp后均为负。5月仅255个候选行且不是完整月份，只能作为尾段压力证据。单位和最大回撤用于同口径比较，不代表真实资金曲线或可同时执行的组合。",
        },
        {"id": "monthly_chart_block", "type": "chart", "chartId": "monthly_chart"},
        {"id": "monthly_table_block", "type": "table", "tableId": "monthly_table"},
        {
            "id": "confidence_finding",
            "type": "markdown",
            "sourceId": "replay_source",
            "body": "## 置信度含有弱排序线索，但不允许事后调门\n\n四分位结果并不单调：Q2好于Q1、Q3最差、Q4最好。最高10%在本池为正，但阈值0.4488是看到经济标签后的分层诊断，不是预注册检测阈值或订单门；后续若利用，只能在新的独立时间块上冻结验证。",
        },
        {"id": "confidence_chart_block", "type": "chart", "chartId": "confidence_chart"},
        {"id": "confidence_table_block", "type": "table", "tableId": "confidence_table"},
        {"id": "outcome_table_block", "type": "table", "tableId": "outcome_table"},
        {
            "id": "methodology",
            "type": "markdown",
            "sourceId": "replay_source",
            "body": "## 方法、基线与复现范围\n\n主基线是同一冻结短向候选池未经过B2筛选的结果；方向性归因使用同币×同月×ATR五分位随机对照。B2是固定阈值检测器，不产生LightGBM AUC；本轮val AUC与单特征基线均不适用。项目要求的top-decile指标在这里按detector confidence诊断，不能替代未来P3判断层排序分数。",
        },
        {"id": "protocol_table_block", "type": "table", "tableId": "protocol_table", "layout": "full"},
        {
            "id": "files",
            "type": "markdown",
            "body": "## 可核对的数据文件\n\n- 密度审计：`analysis/output/p1_b2_density_diagnostic_20260811.json`\n- 全部7,795个已推理proposal rows：`analysis/output/p1_b2_short_l2_backtest_20260811_rows.csv`\n- B2 edge3的3,880个L1 fire rows：`analysis/output/p1_b2_short_l2_backtest_20260811_selected.csv`\n- 3,666个匹配候选结果：`analysis/output/p1_b2_short_l2_backtest_20260811_matched.csv`",
        },
        {
            "id": "caveats",
            "type": "markdown",
            "body": "## 风险与诚实声明\n\n- 本轮没有逐币×逐盘口endpoint连续扫描，因此88.27 fires/日只描述v10 proposal ledger，不能外推成全市场订单/日。\n- 该proposal pool已经预筛且同币间隔至少18根；它不是自然市场基准，但在这个富集池上命中近半已足以判定当前B2密度不可接受。\n- B2权重与conf=0.35来自P1开发期选择；阈值梯度仅用于定位问题，未修改阈值。\n- 收益标签之间可能时间重叠，且把每个fire都当short只是反事实诊断。\n- 未读取holdout，未修改成本/障碍/新鲜度门，未promote、未部署、未下单。",
        },
        {
            "id": "next_steps",
            "type": "markdown",
            "body": "## 建议下一步\n\n1. 当前B2按密度失败处理，不promote，不进入P3判断/执行。\n2. 按交接规范执行P2 hard-negative mining：固定B2 30根窗口与当前事件尺，只把难负例作为单变量加入。\n3. 在不读holdout的独立时间块上增加连续因果tip endpoint密度回放，先冻结并验证L1 fires/day、event precision与去重规则。\n4. 只有P2密度与事件门通过后，才进入P3 LightGBM/规则判断层；不得用提高conf代替重训。",
        },
    ]
    headline = [
        {
            "pool_fire_rate": pool_density["fire_rate"],
            "fires_per_day": pool_density["fires_per_calendar_span_day"],
            "easy_negative_rate": validation_density["easy_negative_endpoint_fire_rate"],
            "validation_recall": density["threshold_sensitivity_diagnostic_only"][0]["validation_recall"],
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
            "density_stages": density_stages,
            "threshold_density": threshold_density,
            "grain": grain,
            "daily": daily.to_dict(orient="records"),
        },
    }
    manifest = {
        "version": 1,
        "surface": "report",
        "title": "Local Signal V2 B2：候选密度与收益诊断",
        "description": "纠正L1 fire与订单口径，并诊断B2在验证尺和预筛proposal pool上的过度触发；不读取holdout。",
        "generatedAt": generated_at,
        "cards": cards,
        "charts": charts,
        "tables": tables,
        "sources": [density_audit_source, replay_source, control_source],
        "blocks": blocks,
    }
    return {
        "surface": "report",
        "manifest": manifest,
        "snapshot": snapshot,
        "sources": [density_audit_source, replay_source, control_source],
    }


def build_markdown(summary: dict, density: dict) -> str:
    primary = summary["selected_primary_edge3_dedup"]
    pool = summary["unfiltered_pool"]
    matched = summary["matched_control"]
    top = summary["detector_confidence_top_decile"]
    top_match = summary["matched_control_top_decile"]
    monthly = summary["monthly"]
    conf = summary["confidence_quartiles"]
    validation = density["selected_threshold_evidence"]["validation"]
    pool_density = density["selected_threshold_evidence"]["proposal_pool"]
    threshold_table = "\n".join(
        f"| {r['threshold']:.2f} | {r['validation_recall']:.2%} | "
        f"{r['validation_easy_negative_fire_rate']:.2%} | {r['proposal_pool_fires']:,} | "
        f"{r['proposal_pool_fires_per_calendar_span_day']:.2f} |"
        for r in density["threshold_sensitivity_diagnostic_only"]
    )
    return f"""# Local Signal V2 B2：候选密度与收益诊断

生成时间：{summary['generated_at']}
结论等级：开发期密度与反事实收益诊断，不是生产回测。

## Executive Summary

- **口径纠正：上一版把3,880写成“交易/开单”是错误的。** 它们是B2在v10预筛proposal ledger上的L1 fire rows，不是订单。连续市场L1触发量与可执行订单数均未计算。
- **但B2检测密度本身确实过高。** conf=0.35命中{pool_density['fires']:,}/7,795个预筛候选（{pool_density['fire_rate']:.2%}），折合proposal ledger {pool_density['fires_per_calendar_span_day']:.2f} fires/日；P1 easy negatives也命中{validation['easy_negative_endpoints_with_any_box']}/{validation['easy_negative_endpoints']}（{validation['easy_negative_endpoint_fire_rate']:.2%}）。
- **不是重复、edge或图像传输bug。** candidate_id唯一，同币最小间隔18根，edge2=edge3；8个样本的数组与PNG推理框数/数值完全一致。
- **提高阈值不能解决。** conf=0.45虽把proposal ledger密度降到8.35 fires/日，但正例召回从73.46%塌到6.98%。
- **阶段纠正：B2当前密度失败。** 下一步是交接规范中的P2 hard-negative mining + 连续因果盘口密度回放；P3 LightGBM/规则判断层在L1密度可信前阻断。

## 数量口径与密度

| 粒度 | 数量 | 含义 |
|---|---:|---|
| P1平衡endpoint尺 | 715 | 358正例 + 357 easy negatives；抽样验证，不是连续市场 |
| v10预筛proposal rows | 7,795 | 230币、已预筛且同币至少间隔18根 |
| B2 L1 fire rows | 3,880 | YOLO候选命中，不是订单 |
| 唯一outcome event groups | 3,715 | 事件组去重只减少4.25% |
| 连续市场L1 fires | 未测 | 未逐币×逐盘口endpoint扫描 |
| 可执行订单 | 未计算 | P2密度未过，P3判断与执行被阻断 |

| conf | 正例召回 | easy-neg命中率 | proposal fires | fires/日 |
|---:|---:|---:|---:|---:|
{threshold_table}

阈值梯度只用于定位问题，未修改冻结阈值。把密度压到个位数/日时召回只剩约7%，所以应通过hard negatives改变模型区分力。

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

## 反事实收益结果

以下结果只是假设“把每个L1 fire row都强行当作short候选”的结果诊断，不是订单回测。

| 范围 | 候选行 | 毛均值bp | 10bp净均值 | 10bp PF | 20bp净均值 | 20bp PF | 20bp胜率 | 单位和最大回撤(20bp) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 未过滤池 | {pool['n']:,} | {pool['mean_gross_bp']:.2f} | {pool['mean_net_taker_10bp']:.2f} | {pool['profit_factor_net_taker']:.3f} | {pool['mean_net_conservative_20bp']:.2f} | {pool['profit_factor_net_conservative']:.3f} | {pool['win_rate_net_conservative']:.2%} | {pool['unit_sum_max_drawdown_conservative']:.3f} |
| B2 edge3 去重 | {primary['n']:,} | {primary['mean_gross_bp']:.2f} | {primary['mean_net_taker_10bp']:.2f} | {primary['profit_factor_net_taker']:.3f} | {primary['mean_net_conservative_20bp']:.2f} | {primary['profit_factor_net_conservative']:.3f} | {primary['win_rate_net_conservative']:.2%} | {primary['unit_sum_max_drawdown_conservative']:.3f} |
| B2 edge2 敏感性 | {summary['selected_sensitivity_edge2_dedup']['n']:,} | {summary['selected_sensitivity_edge2_dedup']['mean_gross_bp']:.2f} | {summary['selected_sensitivity_edge2_dedup']['mean_net_taker_10bp']:.2f} | {summary['selected_sensitivity_edge2_dedup']['profit_factor_net_taker']:.3f} | {summary['selected_sensitivity_edge2_dedup']['mean_net_conservative_20bp']:.2f} | {summary['selected_sensitivity_edge2_dedup']['profit_factor_net_conservative']:.3f} | {summary['selected_sensitivity_edge2_dedup']['win_rate_net_conservative']:.2%} | {summary['selected_sensitivity_edge2_dedup']['unit_sum_max_drawdown_conservative']:.3f} |
| conf 最高10%（诊断） | {top['n']:,} | {top['mean_gross_bp']:.2f} | {top['mean_net_taker_10bp']:.2f} | {top['profit_factor_net_taker']:.3f} | {top['mean_net_conservative_20bp']:.2f} | {top['profit_factor_net_conservative']:.3f} | {top['win_rate_net_conservative']:.2%} | {top['unit_sum_max_drawdown_conservative']:.3f} |

> 最大回撤是按时间排序的“每候选单位收益累加”回撤，不是仓位化资金曲线。候选结果可能重叠，不能解释为可同时执行组合。

## 匹配随机对照

| 范围 | L1 fire rows | 已匹配 | 覆盖率 | 模型20bp净均值 | 随机20bp净均值 | 超额bp | 周块p | 周块 |
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
| 单特征基线 | N/A：P3判断层尚未训练；主基线为未过滤冻结短向候选池 |

## 结果解读

1. 当前首要失败是L1密度：B2在已预筛proposal pool仍命中49.78%，且easy-negative endpoint命中率15.69%。
2. 若把每个fire强行当short，其10bp净均值为{primary['mean_net_taker_10bp']:.2f}bp，且比未过滤池低{primary['mean_net_taker_10bp'] - pool['mean_net_taker_10bp']:.2f}bp/候选；收益诊断也不支持推进。
3. confidence高分段只是事后线索，四分位不单调且跨周不稳定，不能据此抬conf或进入P3。
4. 正确方向是P2 hard-negative mining先修L1区分力，并在独立时间块跑连续tip密度；不是直接训练判断层。

## 数据文件

- `analysis/output/p1_b2_short_l2_backtest_20260811.json`：完整汇总与协议。
- `analysis/output/p1_b2_density_diagnostic_20260811.json`：密度、阈值梯度与实现排错证据。
- `analysis/output/p1_b2_short_l2_backtest_20260811_rows.csv`：7,795 个逐候选 B2 预测。
- `analysis/output/p1_b2_short_l2_backtest_20260811_selected.csv`：3,880 个 L1 fire rows。
- `analysis/output/p1_b2_short_l2_backtest_20260811_matched.csv`：3,666 个匹配候选结果。
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

PYTHONPYCACHEPREFIX=/tmp/fable_pycache PYTHONPATH=.:../yoyo-trading \\
  .venv/bin/python scripts/audit_local_signal_v2_b2_density.py \\
  --device mps --transport-samples 8

PYTHONPATH=.:../yoyo-trading .venv/bin/python \\
  scripts/build_p1_b2_short_l2_backtest_report.py

python3 scripts/md_to_html.py \\
  analysis/p1_b2_short_l2_backtest_20260811.md --out-dir analysis/html
```

## 风险与诚实声明

- 本轮未跑连续市场扫描，88.27 fires/日只描述v10 proposal ledger，不能外推为订单/日。
- proposal pool本身已经预筛；但在这个富集池仍命中近半，已足以判定当前B2密度不可接受。
- B2 权重和 conf=0.35 来自 P1 开发期选择；已额外排除同币所有 val 端点前后72 bars，但剩余数据仍不是最终确认集。
- 置信度四分位和最高10%是事后诊断，禁止自动修改阈值。
- outcome可能时间重叠；把每个fire当short只是反事实诊断，周块检验只有7块。
- 未读取 holdout，未修改成本/障碍/新鲜度门，未 promote、未部署、未下单。

## 下一步

1. 当前B2按密度失败处理：不promote，不进入P3判断/执行。
2. 按交接规范执行P2 hard-negative mining：固定B2 30根窗口、当前事件尺与训练配方，只新增难负例。
3. 在不读holdout的独立时间块做连续因果tip endpoint密度回放；先冻结L1密度门、event匹配与去重口径。
4. 只有P2密度和事件门通过，才进入P3 LightGBM/规则判断层。禁止用提高conf代替重训。
"""


def main() -> int:
    summary = json.loads(SUMMARY_PATH.read_text())
    density = json.loads(DENSITY_PATH.read_text())
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
    artifact = build_artifact(summary, density, daily)
    report_md = build_markdown(summary, density)

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
